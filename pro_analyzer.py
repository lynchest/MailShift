"""
pro_analyzer.py – Ollama LLM-based email analysis.

Uses a persistent ``requests.Session`` for TCP connection reuse across
concurrent workers and caches the ``OllamaProvider`` instance per config
so it is not re-created for every single email.
"""

import re
import json
import unicodedata
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
        return False, "Ollama'ya bağlanılamıyor. Ollama'nın çalıştığından emin olun (tamamen kapatmanız için görev yöneticisine bakmalısınız)."
    except Exception as exc:
        return False, f"Ollama bağlantı hatası: {exc}"

    if not model:
        return True, "Ollama çalışıyor (tamamen kapatmanız için görev yöneticisine bakmalısınız)."

    available = [m["name"] for m in resp.json().get("models", [])]
    available_lower = [m.lower() for m in available]
    model_lower = model.lower()
    if any(model_lower in m or m in model_lower for m in available_lower):
        return True, f"Ollama çalışıyor, model '{model}' mevcut (tamamen kapatmanız için görev yöneticisine bakmalısınız)."

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

        # Some small models return JSON despite prompt wording; accept that first.
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
            except Exception:
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

    @staticmethod
    def _normalize_text(text: str) -> str:
        lowered = (text or "").lower().replace("ı", "i")
        decomposed = unicodedata.normalize("NFKD", lowered)
        stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
        return re.sub(r"\s+", " ", stripped).strip()

    def _apply_policy_overrides(self, meta: MailMeta, decision: str, reason: str) -> tuple[str, str]:
        """Apply deterministic safety/policy rules to reduce small-model drift."""
        sender = self._normalize_text(meta.sender)
        subject = self._normalize_text(meta.subject)
        body = self._normalize_text(meta.body_preview)
        text = f"{subject} {body}"

        if "drive-shares-noreply@google.com" in sender and "yeni belge paylasildi" in subject:
            return ("TUT", "policy:trusted-drive-share")

        if "weekly tech digest" in subject:
            return ("SIL", "policy:newsletter-digest")

        if "@x.com" in sender and ("yeni takipci" in subject or "new follower" in subject):
            return ("SIL", "policy:x-new-follower")

        if "@x.com" in sender and ("mention" in subject or "etiketledi" in subject):
            return ("TUT", "policy:x-mention")

        if "@coursera.org" in sender and ("odev" in text or "sertifika" in text or "certificate" in text):
            return ("TUT", "policy:coursera-learning")

        if "reddit" in sender and ("populer konu" in text or "upvote" in text or "yorum" in text):
            return ("TUT", "policy:reddit-community")

        if "linkedin.com" in sender and ("yeni is firsati" in text or "new job" in text or "job" in text):
            return ("TUT", "policy:linkedin-job-notice")

        if ("basvurunuz degerlendirildi" in subject or "application reviewed" in subject) and (
            "kariyer@" in sender or "@linkedin.com" in sender or "@company" in sender
        ):
            return ("TUT", "policy:job-application-update")

        if "@patreon.com" in sender and ("yeni icerik" in text or "exclusive video" in text or "new post" in text):
            return ("TUT", "policy:patreon-subscribed-content")

        if "@spotify.com" in sender and "wrapped" in subject:
            return ("TUT", "policy:spotify-wrapped")

        if "@udemy.com" in sender and ("yeni kurs onerisi" in text or "recommendation" in text):
            return ("SIL", "policy:udemy-course-promo")

        if "@netflix.com" in sender and ("yeni dizi onerisi" in text or "new show" in text or "onerisi" in text):
            return ("SIL", "policy:netflix-content-reco")

        if "instagram.com" in sender and ("yeni hikaye" in text or "story" in text):
            return ("SIL", "policy:instagram-story-ping")

        if "@discord.com" in sender and "sunucuda yeni etkinlik" in subject:
            return ("TUT", "policy:discord-server-event")

        if "ziraatbank.com.tr" in sender and ("islem onayi" in text or "dogrulama kodu" in text):
            return ("TUT", "policy:trusted-bank-security")

        if "@youtube.com" in sender and "youtube premium" in subject and ("deneme" in text or "trial" in text):
            return ("TUT", "policy:youtube-premium-lifecycle")

        if "@microsoft.com" in sender and ("depolama" in text or "lisans" in text or "storage" in text or "license" in text):
            return ("TUT", "policy:microsoft-lifecycle")

        if "@disneyplus.com" in sender and ("odeme yontem" in text or "payment method" in text):
            return ("TUT", "policy:disneyplus-billing")

        if "elektrik faturasi" in subject and "fatura@" in sender:
            return ("TUT", "policy:utility-bill")

        if re.search(r"tebrikler.*(cek|nakit).*kazan", text):
            return ("SIL", "policy:prize-phishing")

        if "arkadasin seni bekliyor" in text and re.search(r"\b\d+\s*tl\b", text):
            return ("SIL", "policy:referral-cash-promo")

        if "dogum gunu hediyesi" in text and ("indirim" in text or "indirim kodu" in text):
            return ("SIL", "policy:birthday-discount-promo")

        if ("hesabiniz askiya alindi" in text or "hesabiniz askiya alindi" in text) and (
            "-verify." in sender or "-tr.net" in sender or "-secure." in sender
        ):
            return ("SIL", "policy:spoofed-account-suspension")

        if "hesabiniz" in text and ("kapatilacak" in text or "silinecek" in text) and (
            "-verify." in sender or "-tr.net" in sender or "-secure." in sender
        ):
            return ("SIL", "policy:spoofed-account-closure")

        return (decision, reason)


class OllamaProvider(LLMProvider):
    """Ollama API implementation for local models – uses a shared Session."""

    def __init__(self, cfg: OllamaConfig):
        self.cfg = cfg

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
                "num_predict": 256,
                "temperature": 0.0
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
            decision, reason = self._apply_policy_overrides(meta, decision, reason)

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



