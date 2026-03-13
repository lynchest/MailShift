"""
pro_analyzer.py – Ollama LLM-based email analysis.

Uses a persistent ``requests.Session`` for TCP connection reuse across
concurrent workers and caches the ``OllamaProvider`` instance per config
so it is not re-created for every single email.
"""

import re
from typing import Optional

import requests

from config import OllamaConfig
from models import MailMeta, ScanResult
from abc import ABC, abstractmethod
from logger import log

# ── Module-level connection pool ─────────────────────────────────────────
_session: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    """Return (or lazily create) a module-level requests.Session."""
    global _session
    if _session is None:
        _session = requests.Session()
        # Allow up to 32 concurrent connections to the same Ollama host
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
    """
    Verify that Ollama is reachable and (optionally) the requested model
    is available.

    Returns ``(ok, message)`` – *ok* is ``True`` when Ollama is ready.
    """
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
        upper_response = response.upper()

        has_sil = "SIL" in upper_response
        has_tut = "TUT" in upper_response

        if has_sil and not has_tut:
            decision = "SIL"
        elif has_tut and not has_sil:
            decision = "TUT"
        elif has_sil and has_tut:
            decision = "SIL" if upper_response.find("SIL") < upper_response.find("TUT") else "TUT"
        else:
            return ("TUT", "invalid-response")

        reason = self._extract_reason(response, decision)
        return (decision, reason)

    def _extract_reason(self, response: str, decision: str) -> str:
        response_lower = response.lower()
        patterns = [
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
                if 3 < len(reason) < 50:
                    return reason
        if decision == "SIL":
            return "newsletter/spam"
        return "personal/important"


class OllamaProvider(LLMProvider):
    """Ollama API implementation for local models – uses a shared Session."""

    def __init__(self, cfg: OllamaConfig):
        self.cfg = cfg

    def analyze(self, meta: MailMeta) -> ScanResult:
        snippet = meta.body_preview or f"{meta.subject} {meta.sender}"
        payload = {
            "model": self.cfg.model,
            "system": self.cfg.system_prompt,
            "prompt": snippet[: self.cfg.max_body_chars],
            "stream": False,
            "options": {
                "num_predict": 64,
                "temperature": 0.0
            }
        }
        try:
            resp = requests.post(
                f"{self.cfg.base_url}/api/generate",
                json=payload,
                timeout=self.cfg.timeout,
            )
            resp.raise_for_status()
            raw_response = resp.json().get("response", "").strip()

            decision, reason = self._parse_llm_response(raw_response)

            return ScanResult(mail=meta, decision=decision, reason=f"llm:{decision} - {reason}")
        except Exception as exc:
            log.warning(f"Ollama API error: {exc}")
            return ScanResult(mail=meta, decision="TUT", reason=f"llm-error:{exc}")


# ── Cached provider instance ─────────────────────────────────────────────
_provider_cache: dict[str, OllamaProvider] = {}


def pro_analyze(meta: MailMeta, ollama_cfg: OllamaConfig) -> ScanResult:
    """Analyze a single email via Ollama (cached provider instance)."""
    cache_key = f"{ollama_cfg.base_url}|{ollama_cfg.model}"
    if cache_key not in _provider_cache:
        _provider_cache[cache_key] = OllamaProvider(ollama_cfg)
    return _provider_cache[cache_key].analyze(meta)



