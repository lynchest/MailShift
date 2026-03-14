"""
pro_analyzer.py – Ollama LLM-based email analysis.

Uses a persistent ``requests.Session`` for TCP connection reuse across
concurrent workers and caches the ``OllamaProvider`` instance per config
so it is not re-created for every single email.
"""

import re
import json
import os
import unicodedata
from typing import Optional

import requests

from config import OllamaConfig
from hardware import detect_model_size, get_system_info
from models import MailMeta, ScanResult
from abc import ABC, abstractmethod
from logger import log

# ── Module-level connection pool ─────────────────────────────────────────
_session: Optional[requests.Session] = None


def _parse_bool_env(value: str) -> Optional[bool]:
    lowered = (value or "").strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return None


def _select_ollama_runtime_options(model_name: str) -> dict[str, int | float | bool]:
    """Pick Ollama runtime options based on current machine capabilities."""
    options: dict[str, int | float | bool] = {
        "num_predict": 256,
        "temperature": 0.0,
        "num_thread": 6,
        "num_gpu": 32,
        "use_flash_attn": True,
    }

    try:
        system_info = get_system_info()
        model_size_b = detect_model_size(model_name)

        cpu_threads = max(2, min(12, max(1, system_info.cpu_count - 1)))
        options["num_thread"] = cpu_threads

        if system_info.has_gpu and system_info.vram_available_gb > 0.5:
            estimated_model_vram = max(1.0, model_size_b * 0.9)
            fit_ratio = system_info.vram_available_gb / estimated_model_vram

            if fit_ratio >= 1.5:
                options["num_gpu"] = 32
            elif fit_ratio >= 1.0:
                options["num_gpu"] = 24
            elif fit_ratio >= 0.6:
                options["num_gpu"] = 16
            else:
                options["num_gpu"] = 8

            gpu_name = (system_info.gpu_name or "").lower()
            options["use_flash_attn"] = any(token in gpu_name for token in ("nvidia", "rtx", "gtx", "apple"))
            options["num_thread"] = max(2, min(int(options["num_thread"]), 8))
        else:
            options["num_gpu"] = 0
            options["use_flash_attn"] = False
    except Exception as exc:
        log.warning(f"Hardware-based Ollama tuning failed, defaults will be used: {exc}")

    env_num_thread = os.getenv("MAILSHIFT_OLLAMA_NUM_THREAD", "").strip()
    if env_num_thread.isdigit() and int(env_num_thread) > 0:
        options["num_thread"] = int(env_num_thread)

    env_num_gpu = os.getenv("MAILSHIFT_OLLAMA_NUM_GPU", "").strip()
    if env_num_gpu.isdigit() and int(env_num_gpu) >= 0:
        options["num_gpu"] = int(env_num_gpu)

    env_flash = _parse_bool_env(os.getenv("MAILSHIFT_OLLAMA_USE_FLASH_ATTN", ""))
    if env_flash is not None:
        options["use_flash_attn"] = env_flash

    return options


def _get_session() -> requests.Session:
    """Return (or lazily create) a module-level requests.Session."""
    global _session
    if _session is None:
        _session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=1,
            pool_maxsize=32,
            max_retries=requests.adapters.Retry(
                total=2, backoff_factor=0.3,
                status_forcelist=[502, 503, 504],
            ),
        )
        _session.mount("http://", adapter)
        _session.mount("https://", adapter)
    return _session


# ── Ollama health check ──────────────────────────────────────────────────

def check_ollama_health(base_url: str = "http://localhost:11434", model: str = "") -> tuple[bool, str]:
    try:
        resp = _get_session().get(f"{base_url}/api/tags", timeout=5)
        resp.raise_for_status()
    except requests.ConnectionError:
        return False, "Ollama'ya bağlanılamıyor. Ollama'nın çalıştığından emin olun."
    except Exception as exc:
        return False, f"Ollama bağlantı hatası: {exc}"

    if not model:
        return True, "Ollama çalışıyor."

    available = [m["name"] for m in resp.json().get("models", [])]
    available_lower = [m.lower() for m in available]
    model_lower = model.lower()
    if any(model_lower in m or m in model_lower for m in available_lower):
        return True, f"Ollama çalışıyor, model '{model}' mevcut."

    return False, (
        f"Ollama çalışıyor fakat '{model}' modeli bulunamadı.\n"
        f"Mevcut modeller: {', '.join(available) or '(yok)'}\n"
        f"Modeli çekmek için: ollama pull {model}"
    )


# ── LLM Provider abstraction ─────────────────────────────────────────────

class LLMProvider(ABC):
    """Base interface for LLM-based email analysis."""

    @abstractmethod
    def analyze(self, meta: MailMeta) -> ScanResult:
        """Analyze an email and return a ScanResult."""

    def _parse_llm_response(self, response: str) -> tuple[str, str]:
        """Shared parser logic for LLM decisions."""
        response = (response or "").strip()
        if not response:
            return ("TUT", "invalid-response")

        # 1. Try direct JSON parsing first (Structured Outputs)
        try:
            parsed = json.loads(response)
            if isinstance(parsed, dict) and "decision" in parsed:
                decision = str(parsed["decision"]).upper()
                if decision in {"SIL", "TUT"}:
                    reason = str(parsed.get("reason", "no reason provided"))
                    return (decision, reason)
        except json.JSONDecodeError:
            pass # Fallback to manual extraction

        # 2. Fallback to extracting from pseudo-JSON or plain text
        decision = self._extract_decision_from_json(response)
        if decision is None:
            normalized = self._normalize_for_decision_parse(response)
            matches = list(re.finditer(r"\b(sil|tut)\b", normalized, flags=re.IGNORECASE))
            if not matches:
                return ("TUT", "invalid-response")
            first = matches[0].group(1).lower()
            decision = "SIL" if first == "sil" else "TUT"

        reason = self._extract_reason(response, decision)
        return (decision, reason)

    def _extract_decision_from_json(self, response: str) -> Optional[str]:
        """Extract SIL/TUT from JSON-like model responses."""
        candidates = [response]
        block_match = re.search(r"\{.*\}", response, flags=re.DOTALL)
        if block_match:
            candidates.insert(0, block_match.group(0))

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue

            values_to_check: list[str] = []
            if isinstance(parsed, dict):
                for key in ("decision", "karar", "label", "result"):
                    value = parsed.get(key)
                    if isinstance(value, str):
                        values_to_check.append(value)
            elif isinstance(parsed, str):
                values_to_check.append(parsed)

            for value in values_to_check:
                normalized = self._normalize_for_decision_parse(value)
                if re.search(r"\bsil\b", normalized, flags=re.IGNORECASE):
                    return "SIL"
                if re.search(r"\btut\b", normalized, flags=re.IGNORECASE):
                    return "TUT"

        return None

    @staticmethod
    def _normalize_for_decision_parse(text: str) -> str:
        """Normalize Turkish dotted/dotless i so SİL/SIL/sıl all match."""
        lowered = text.lower().replace("ı", "i")
        decomposed = unicodedata.normalize("NFKD", lowered)
        return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")

    def _extract_reason(self, response: str, decision: str) -> str:
        response_lower = response.lower()
        # Added Turkish keywords as well
        patterns = [
            r'çünkü\s+(.+?)(?:\.|$)',
            r'nedeni[:\s]\s*(.+?)(?:\.|$)',
            r'sebebi[:\s]\s*(.+?)(?:\.|$)',
            r'because\s+(.+?)(?:\.|$)',
            r'since\s+(.+?)(?:\.|$)',
            r'reason:\s*(.+?)(?:\.|$)',
            r'it is\s+(a\s+\w+)\s+',
            r'this is\s+(a\s+\w+)\s+',
        ]
        for pattern in patterns:
            match = re.search(pattern, response_lower)
            if match:
                reason = match.group(1).strip()
                if 3 < len(reason) < 150: # Slightly increased max reason length
                    return reason
        if decision == "SIL":
            return "newsletter/spam"
        return "personal/important"


class OllamaProvider(LLMProvider):
    """Ollama API implementation for local models – uses a shared Session."""

    def __init__(self, cfg: OllamaConfig):
        self.cfg = cfg
        self.runtime_options = _select_ollama_runtime_options(cfg.model)

    @staticmethod
    def _build_user_prompt(meta: MailMeta, max_body_chars: int) -> str:
        body = (meta.body_preview or "")[:max_body_chars]
        return (
            "Aşağıdaki e-postayı sınıflandır. Sadece karar ver.\n\n"
            f"Konu: {meta.subject}\n"
            f"Gönderen: {meta.sender}\n"
            f"İçerik: {body}"
        )

    def analyze(self, meta: MailMeta) -> ScanResult:
        user_prompt = self._build_user_prompt(meta, self.cfg.max_body_chars)
        payload = {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": self.cfg.system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "think": False,
            "format": {
                "type": "object",
                "properties": {
                    "decision": {"type": "string", "enum": ["SIL", "TUT"]},
                    "reason": {"type": "string"},
                },
                "required": ["decision"],
            },
            "options": {
                **self.runtime_options,
            }
        }
        try:
            resp = _get_session().post(
                f"{self.cfg.base_url}/api/chat",
                json=payload,
                timeout=self.cfg.timeout,
            )
            resp.raise_for_status()
            body = resp.json()
            raw_response = ""
            message = body.get("message")
            if isinstance(message, dict):
                raw_response = str(message.get("content", "")).strip()
            if not raw_response:
                raw_response = str(body.get("response", "")).strip()

            decision, reason = self._parse_llm_response(raw_response)
            return ScanResult(mail=meta, decision=decision, reason=f"llm:{decision} - {reason}")
            
        except Exception as exc:
            log.warning(f"Ollama API error: {exc}")
            return ScanResult(mail=meta, decision="TUT", reason=f"llm-error:{exc}")


# ── Cached provider instance & Main Entry ────────────────────────────────
_provider_cache: dict[str, OllamaProvider] = {}


def pro_analyze(meta: MailMeta, ollama_cfg: OllamaConfig) -> ScanResult:
    """Analyze a single email via cached Ollama provider."""
    cache_key = f"{ollama_cfg.base_url}|{ollama_cfg.model}"
    if cache_key not in _provider_cache:
        _provider_cache[cache_key] = OllamaProvider(ollama_cfg)
    return _provider_cache[cache_key].analyze(meta)