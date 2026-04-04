"""
pro_analyzer.py | LLM-based email analysis for MailShift.

Supports two backends:
  - Ollama  (recommended for NVIDIA / Apple Silicon)
  - LM Studio (recommended for AMD / Intel GPU users, OpenAI-compatible API)
"""

import re
import json
import os
import unicodedata
import threading
from typing import Optional, Union, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from abc import ABC, abstractmethod

from ...config.config import OllamaConfig, LMStudioConfig
from ...utils.hardware import detect_model_size, get_system_info
from ...models.models import MailMeta, ScanResult
from ...utils.logger import log

# ── Module-level connection pool & locks ─────────────────────────────────
_session: Optional[requests.Session] = None
_session_lock = threading.Lock()

_provider_cache: dict[str, "LLMProvider"] = {}
_provider_cache_lock = threading.Lock()

# ── Compiled patterns for reason extraction ──────────────────────────────
REASON_PATTERNS = [
    re.compile(r'çünkü\s+(.+?)(?:\.|$)', re.IGNORECASE),
    re.compile(r'nedeni[:\s]\s*(.+?)(?:\.|$)', re.IGNORECASE),
    re.compile(r'sebebi[:\s]\s*(.+?)(?:\.|$)', re.IGNORECASE),
    re.compile(r'because\s+(.+?)(?:\.|$)', re.IGNORECASE),
    re.compile(r'since\s+(.+?)(?:\.|$)', re.IGNORECASE),
    re.compile(r'reason:\s*(.+?)(?:\.|$)', re.IGNORECASE),
    re.compile(r'it is\s+(a\s+\w+)\s+', re.IGNORECASE),
    re.compile(r'this is\s+(a\s+\w+)\s+', re.IGNORECASE),
]


def is_llm_timeout_reason(reason: str) -> bool:
    """Return True when a result reason indicates timeout-like LLM failure."""
    normalized = (reason or "").strip().lower()
    if not normalized:
        return False
    return (
        normalized.startswith("llm-timeout")
        or " timed out" in normalized
        or "timeout" in normalized
    )


def is_llm_error_reason(reason: str) -> bool:
    """Return True when a result reason indicates non-timeout LLM/API failure."""
    normalized = (reason or "").strip().lower()
    if not normalized:
        return False
    return normalized.startswith("llm-error")

def _parse_bool_env(value: str) -> Optional[bool]:
    lowered = (value or "").strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return None

def _select_ollama_runtime_options(model_name: str) -> dict[str, Union[int, float, bool]]:
    """Pick Ollama runtime options based on current machine capabilities."""
    options: dict[str, Union[int, float, bool]] = {
        "num_predict": 96,
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

    # Environment variable overrides
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
    """Return (or lazily create) a thread-safe module-level requests.Session."""
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                _session = requests.Session()
                # Configured Retry to include POST methods explicitly
                retry_strategy = Retry(
                    total=2, 
                    backoff_factor=0.3,
                    status_forcelist=[429, 500, 502, 503, 504],
                    allowed_methods=["GET", "POST"]
                )
                adapter = HTTPAdapter(
                    pool_connections=10,
                    pool_maxsize=50,
                    max_retries=retry_strategy,
                )
                _session.mount("http://", adapter)
                _session.mount("https://", adapter)
    return _session

def close_ollama_session():
    """Close the persistent HTTP session if it exists."""
    global _session
    with _session_lock:
        if _session is not None:
            _session.close()
            _session = None

# ── Health Checks & Memory Management ────────────────────────────────────

def check_ollama_health(base_url: str = "http://localhost:11434", model: str = "") -> Tuple[bool, str]:
    try:
        resp = _get_session().get(f"{base_url}/api/tags", timeout=5)
        resp.raise_for_status()
    except requests.ConnectionError:
        return False, "Ollama'ya bağlanılamıyor. Çalıştığından emin olun."
    except Exception as exc:
        return False, f"Ollama bağlantı hatası: {exc}"

    if not model:
        return True, "Ollama çalışıyor."

    available = [m["name"].lower() for m in resp.json().get("models", [])]
    model_lower = model.lower()
    if any(model_lower in m or m in model_lower for m in available):
        return True, f"Ollama çalışıyor, model '{model}' mevcut."

    return False, f"Ollama çalışıyor fakat '{model}' modeli bulunamadı."

def check_lm_studio_health(base_url: str = "http://localhost:1234", model: str = "") -> Tuple[bool, str]:
    try:
        resp = _get_session().get(f"{base_url}/v1/models", timeout=5)
        resp.raise_for_status()
    except requests.ConnectionError:
        return False, "LM Studio'ya bağlanılamıyor. Local Server'ın açık olduğundan emin olun."
    except Exception as exc:
        return False, f"LM Studio bağlantı hatası: {exc}"

    available = [m["id"] for m in resp.json().get("data", [])]
    if not model:
        if available:
            return True, f"LM Studio çalışıyor, {len(available)} model yüklü."
        return False, "LM Studio çalışıyor fakat yüklü model yok."

    if any(model.lower() in m.lower() or m.lower() in model.lower() for m in available):
        return True, f"LM Studio çalışıyor, model '{model}' mevcut."

    return False, f"LM Studio çalışıyor fakat '{model}' modeli yüklü değil."

def unload_lm_studio_models(base_url: str = "http://localhost:1234", model_id: Optional[str] = None) -> list[str]:
    unloaded = []
    try:
        resp = _get_session().get(f"{base_url}/v1/models", timeout=3)
        if resp.status_code != 200:
            return []

        for m in resp.json().get("data", []):
            m_id = m.get("id")
            if not m_id or (model_id and model_id.lower() not in m_id.lower()):
                continue

            try:
                unload_resp = _get_session().post(
                    f"{base_url}/api/v1/models/unload",
                    json={"instance_id": m_id},
                    timeout=3
                )
                if unload_resp.status_code == 200:
                    unloaded.append(m_id)
            except Exception:
                continue
    except Exception as exc:
        log.debug(f"LM Studio model tahliye işlemi başarısız: {exc}")

    return unloaded

def unload_ollama_model(base_url: str = "http://localhost:11434", model: str = ""):
    if not model:
        return
    try:
        _get_session().post(
            f"{base_url}/api/chat",
            json={"model": model, "messages": [], "keep_alive": 0},
            timeout=3
        )
    except Exception as exc:
        log.debug(f"Ollama model tahliye işlemi başarısız: {exc}")

# ── LLM Provider Abstraction ─────────────────────────────────────────────

class LLMProvider(ABC):
    """Base interface for LLM-based email analysis."""

    @abstractmethod
    def analyze(
        self,
        meta: MailMeta,
        fast_reason: str = "",
        fast_category: str = "",
        cancel_event: Optional[threading.Event] = None,
    ) -> ScanResult:
        """Analyze an email and return a ScanResult."""

    def _build_user_prompt(
        self,
        meta: MailMeta,
        max_body_chars: int,
        fast_reason: str = "",
        fast_category: str = "",
    ) -> str:
        """Shared prompt builder moved to Base Class (DRY Principle)."""
        body = (meta.body_preview or "")[:max_body_chars]
        hint = ""
        if fast_reason:
            hint = f"\nÖn Analiz (heuristik): Bu e-posta '{fast_reason}' kuralıyla SIL olarak işaretlendi."
            if fast_category:
                hint += f" Kategori: {fast_category}."
            hint += "\n"

        return (
            "Aşağıdaki e-postayı sınıflandır. Sadece karar ver.\n\n"
            f"Konu: {meta.subject}\n"
            f"Gönderen: {meta.sender}\n"
            f"İçerik: {body}\n"
            f"{hint}"
        ).strip()

    def _parse_llm_response(self, response: str) -> Tuple[str, str]:
        response = (response or "").strip()
        if not response:
            return ("TUT", "invalid-response")

        raw_thinking = ""
        think_match = re.search(r"<think>([\s\S]*?)</think>", response, flags=re.IGNORECASE)
        if think_match:
            raw_thinking = think_match.group(1).strip()
            response = response.replace(think_match.group(0), "").strip()

        prefix = ""
        if raw_thinking:
            sanitized = re.sub(r"\s+", " ", raw_thinking.replace("\n", " ").replace("\r", " ")).strip()
            if len(sanitized) > 100:
                sanitized = sanitized[:97] + "..."
            prefix = f"({sanitized}) "

        try:
            parsed = json.loads(response)
            if isinstance(parsed, dict) and "decision" in parsed:
                decision = str(parsed["decision"]).upper()
                if decision in {"SIL", "TUT"}:
                    reason = str(parsed.get("reason", "no reason provided"))
                    return (decision, f"{prefix}{reason}")
        except json.JSONDecodeError:
            pass

        decision = self._extract_decision_from_json(response)
        if decision is None:
            normalized = self._normalize_for_decision_parse(response)
            matches = list(re.finditer(r"\b(sil|tut)\b", normalized, flags=re.IGNORECASE))
            if not matches:
                return ("TUT", "invalid-response")
            decision = "SIL" if matches[0].group(1).lower() == "sil" else "TUT"

        reason = self._extract_reason(response, decision)
        return (decision, f"{prefix}{reason}")

    def _extract_decision_from_json(self, response: str) -> Optional[str]:
        candidates = [response]
        # Regex optimized for performance (non-greedy structural match)
        block_match = re.search(r"\{[\s\S]*\}", response)
        if block_match:
            candidates.insert(0, block_match.group(0))

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue

            values_to_check: list[str] = []
            if isinstance(parsed, dict):
                values_to_check.extend(
                    str(v) for k, v in parsed.items() 
                    if k in {"decision", "karar", "label", "result"} and isinstance(v, str)
                )
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
        lowered = text.lower().replace("ı", "i")
        decomposed = unicodedata.normalize("NFKD", lowered)
        return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")

    def _extract_reason(self, response: str, decision: str) -> str:
        response_lower = response.lower()
        for pattern in REASON_PATTERNS:
            match = pattern.search(response_lower)
            if match:
                reason = match.group(1).strip()
                if 3 < len(reason) < 150:
                    return reason
        return "newsletter/spam" if decision == "SIL" else "personal/important"

class OllamaProvider(LLMProvider):
    def __init__(self, cfg: OllamaConfig):
        self.cfg = cfg
        self.runtime_options = _select_ollama_runtime_options(cfg.model)

    def _resolve_num_predict(self) -> int:
        if self.cfg.use_think:
            return 512

        # qwen3.5 small models can return empty content when budget is too low.
        model_name = (self.cfg.model or "").lower()
        if "qwen3.5:2b" in model_name or "qwen3.5:4b" in model_name:
            return 256
        return 96

    @staticmethod
    def _extract_ollama_response_text(body: dict) -> str:
        message = body.get("message")
        message_content = ""
        if isinstance(message, dict):
            content_value = message.get("content", "")
            if isinstance(content_value, str):
                message_content = content_value.strip()
            elif content_value is not None:
                message_content = str(content_value).strip()

        if message_content:
            return message_content

        # Some Ollama builds can return empty message.content while a top-level
        # textual response still exists.
        response_value = body.get("response", "")
        if isinstance(response_value, str):
            return response_value.strip()
        if response_value is not None:
            return str(response_value).strip()

        return ""

    def analyze(
        self,
        meta: MailMeta,
        fast_reason: str = "",
        fast_category: str = "",
        cancel_event: Optional[threading.Event] = None,
    ) -> ScanResult:
        if cancel_event and cancel_event.is_set():
            return ScanResult(mail=meta, decision="TUT", reason="cancelled")

        user_prompt = self._build_user_prompt(meta, self.cfg.max_body_chars, fast_reason, fast_category)

        payload = {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": self.cfg.system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "think": self.cfg.use_think,
            "keep_alive": -1,
            "format": "json",
            "options": {
                **self.runtime_options,
                "num_predict": self._resolve_num_predict(),
            }
        }
        
        # Requests Session allows max_retries natively, but we keep explicit timeout catching for logging.
        try:
            resp = _get_session().post(
                f"{self.cfg.base_url}/api/chat",
                json=payload,
                timeout=self.cfg.timeout,
            )
            resp.raise_for_status()
            body = resp.json()

            raw_response = self._extract_ollama_response_text(body)

            decision, reason = self._parse_llm_response(raw_response)
            return ScanResult(mail=meta, decision=decision, reason=f"llm:{decision} - {reason}")

        except requests.Timeout:
            log.warning(f"Ollama timeout for UID {meta.uid}")
            return ScanResult(mail=meta, decision="TUT", reason="llm-timeout")
        except Exception as exc:
            log.warning(f"Ollama API error: {exc}")
            return ScanResult(mail=meta, decision="TUT", reason=f"llm-error:{exc}")

class LMStudioProvider(LLMProvider):
    def __init__(self, cfg: LMStudioConfig):
        self.cfg = cfg

    def analyze(
        self,
        meta: MailMeta,
        fast_reason: str = "",
        fast_category: str = "",
        cancel_event: Optional[threading.Event] = None,
    ) -> ScanResult:
        if cancel_event and cancel_event.is_set():
            return ScanResult(mail=meta, decision="TUT", reason="cancelled")

        user_prompt = self._build_user_prompt(meta, self.cfg.max_body_chars, fast_reason, fast_category)

        payload = {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": self.cfg.system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "temperature": 0.0,
            "max_tokens": 512 if self.cfg.use_think else 256,
        }

        try:
            resp = _get_session().post(
                f"{self.cfg.base_url}/v1/chat/completions",
                json=payload,
                timeout=self.cfg.timeout,
            )
            resp.raise_for_status()
            
            choices = resp.json().get("choices", [])
            raw_response = ""
            if choices and isinstance(choices[0], dict):
                raw_response = str(choices[0].get("message", {}).get("content", "")).strip()

            decision, reason = self._parse_llm_response(raw_response)
            return ScanResult(mail=meta, decision=decision, reason=f"llm:{decision} - {reason}")

        except requests.Timeout:
            log.warning(f"LM Studio timeout for UID {meta.uid}")
            return ScanResult(mail=meta, decision="TUT", reason="llm-timeout")
        except Exception as exc:
            log.warning(f"LM Studio API error: {exc}")
            return ScanResult(mail=meta, decision="TUT", reason=f"llm-error:{exc}")

# ── Main Entry ───────────────────────────────────────────────────────────

def pro_analyze(
    meta: MailMeta,
    cfg: Union[OllamaConfig, LMStudioConfig],
    backend: str = "ollama",
    fast_reason: str = "",
    fast_category: str = "",
    cancel_event: Optional[threading.Event] = None,
) -> ScanResult:
    """Analyze a single email via thread-safe cached LLM provider."""
    cache_key = f"{backend}|{cfg.base_url}|{cfg.model}"
    
    with _provider_cache_lock:
        if cache_key not in _provider_cache:
            if backend == "lm_studio":
                _provider_cache[cache_key] = LMStudioProvider(cfg)
            else:
                _provider_cache[cache_key] = OllamaProvider(cfg)
                
    provider = _provider_cache[cache_key]
    
    return provider.analyze(
        meta,
        fast_reason=fast_reason,
        fast_category=fast_category,
        cancel_event=cancel_event,
    )