"""
config.py – Pydantic-based configuration models for MailShift.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class Provider(str, Enum):
    GMAIL = "gmail"
    PROTON = "proton"
    CUSTOM = "custom"


class Mode(str, Enum):
    FAST = "fast"
    PRO = "pro"


class IMAPConfig(BaseModel):
    """IMAP connection parameters."""

    host: str
    port: int = 993
    use_ssl: bool = True
    username: str
    password: SecretStr

    model_config = ConfigDict(frozen=True)


PROVIDER_DEFAULTS: dict[Provider, dict] = {
    Provider.GMAIL: {
        "host": "imap.gmail.com",
        "port": 993,
        "use_ssl": True,
    },
    Provider.PROTON: {
        "host": "127.0.0.1",
        "port": 1143,
        "use_ssl": False,
    },
    Provider.CUSTOM: {
        "host": "",
        "port": 993,
        "use_ssl": True,
    },
}

# ---------------------------------------------------------------------------
# Heuristic keyword lists (case-insensitive)
# ---------------------------------------------------------------------------

import json
from pathlib import Path


def _load_keywords(filename: str) -> list[str]:
    path = Path(__file__).parent / filename
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_keywords(filename: str, keywords: list[str]) -> None:
    path = Path(__file__).parent / filename
    with open(path, "w", encoding="utf-8") as f:
        json.dump(keywords, f, ensure_ascii=False, indent=2)


def add_to_whitelist(word: str) -> bool:
    keywords = _load_keywords("whitelist.json")
    if word not in keywords:
        keywords.append(word)
        _save_keywords("whitelist.json", keywords)
        return True
    return False


def remove_from_whitelist(word: str) -> bool:
    keywords = _load_keywords("whitelist.json")
    if word in keywords:
        keywords.remove(word)
        _save_keywords("whitelist.json", keywords)
        return True
    return False


def add_to_blacklist(word: str) -> bool:
    keywords = _load_keywords("blacklist.json")
    if word not in keywords:
        keywords.append(word)
        _save_keywords("blacklist.json", keywords)
        return True
    return False


def remove_from_blacklist(word: str) -> bool:
    keywords = _load_keywords("blacklist.json")
    if word in keywords:
        keywords.remove(word)
        _save_keywords("blacklist.json", keywords)
        return True
    return False


def list_keywords() -> tuple[list[str], list[str]]:
    whitelist = _load_keywords("whitelist.json")
    blacklist = _load_keywords("blacklist.json")
    return whitelist, blacklist


JUNK_KEYWORDS: list[str] = _load_keywords("blacklist.json")
WHITELIST_KEYWORDS: list[str] = _load_keywords("whitelist.json")

import re

JUNK_KEYWORDS_LOWER: list[str] = [k.lower() for k in JUNK_KEYWORDS]
WHITELIST_KEYWORDS_LOWER: list[str] = [k.lower() for k in WHITELIST_KEYWORDS]

_JUNK_ESCAPED = [re.escape(k) for k in JUNK_KEYWORDS_LOWER]
_WHITELIST_ESCAPED = [re.escape(k) for k in WHITELIST_KEYWORDS_LOWER]

JUNK_PATTERN = re.compile('|'.join(_JUNK_ESCAPED), re.IGNORECASE) if _JUNK_ESCAPED else None
WHITELIST_PATTERN = re.compile('|'.join(_WHITELIST_ESCAPED), re.IGNORECASE) if _WHITELIST_ESCAPED else None
DEFAULT_SYSTEM_PROMPT = """
Sen bir e-posta temizleme asistanısın. E-postaları SIL veya TUT olarak sınıflandır.

JSON formatında cevap ver: {"decision": "SIL" | "TUT", "reason": "kısa açıklama"}

KRİTİK SIL (KESİNLİKLE SİLİNECEKLER):
1. PAZARLAMA/SATIŞ: İndirim, kampanya, sepet uyarısı ("Sepetinde ürün var"), indirim kodu.
2. GAMIFICATION/STRATEJİ: "Serin 45 güne ulaştı", "Duo seni özledi", "Rozet kazandın", "Tamamla kazan".
3. SADAKAT: "Elite oldun", "Puanların siliniyor", "VIP davet".
4. BÜLTEN VE ÖZET (newsletter): "Weekly Tech Digest", "Daily Digest", "Topluluğu Bülteni", "Haftalık Bülten".
5. RESMİ AMA ÖNEMSİZ: Belediye etkinlikleri (İftar, kutlama), TÜİK gibi kurumların sadece veri yayınlama duyuruları.
6. SUBSTACK: Eğer "Yeni ücretli yazı" başlığı olsa bile içerik bir kişisel iletişim değil toplu bültense SIL yap.

TUT (SADECE BUNLAR):
1. Kişisel: Gerçek kişiden gelen mesaj, direkt sana yönelik @mention veya yorum yanıtı.
2. Operasyonel: Fatura, dekont, banka ekstresi, şifre sıfırlama, OTP, kargo takibi, resmi vergi bildirimi.
3. Kritik: Abonelik iptali veya ücretli yenileme uyarısı.

ÖRNEKLER:
- "Weekly Tech Digest" -> SIL (Bülten)
- "Haftalık Bülten: Python Topluluğu" -> SIL (Bülten)
- "Belediye: Ramazan iftar programı" -> SIL (Resmi/Genel Duyuru)
- "TÜİK: Enflasyon verisi yayımlandı" -> SIL (Veri bülteni)
- "Duolingo: Serin 45 güne ulaştı!" -> SIL (Gamification)
- "Migros Hemen: Sepetinde ürünler var" -> SIL (Satış)
- "IKEA Aile Puanlarınız siliniyor!" -> SIL (Sadakat)
- "Substack: Yeni ücretli yazı" -> SIL (Toplu gönderim/Bülten)
- "Profilini Tamamla, 100 TL Kazan" -> SIL (Strateji)
"""


class OllamaConfig(BaseModel):
    """Settings for the local Ollama LLM endpoint."""

    base_url: str = "http://localhost:11434"
    model: str = "Qwen3.5:0.8B"
    timeout: int = 60
    max_body_chars: int = 500

    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    use_think: bool = False

    model_config = ConfigDict(frozen=True)


class LMStudioConfig(BaseModel):
    """Settings for the LM Studio OpenAI-compatible endpoint."""

    base_url: str = "http://localhost:1234"
    model: str = ""
    timeout: int = 60
    max_body_chars: int = 500

    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    use_think: bool = False

    model_config = ConfigDict(frozen=True)


class RateLimitConfig(BaseModel):
    """Rate limiting and retry settings for IMAP operations."""

    # Chunk sizing
    fetch_chunk_size: int = 100   # UIDs per IMAP fetch request
    delete_chunk_size: int = 100  # UIDs per IMAP store/copy request

    # Rate limiting: delay between consecutive IMAP chunk requests (seconds)
    chunk_delay: float = 0.1      # 100 ms between chunks by default

    # Retry / back-off
    max_retries: int = 3          # Number of retry attempts per chunk
    retry_backoff: float = 2.0    # Exponential back-off multiplier (s, s*2, s*4 …)

    # Connection timeout (seconds) – applied to the underlying socket
    connect_timeout: int = 30

    # Database batch-commit size: how many mails to INSERT at once
    db_batch_size: int = 500

    model_config = ConfigDict(frozen=True)


class AppConfig(BaseModel):
    """Top-level application configuration."""

    provider: Provider
    mode: Mode
    imap: IMAPConfig
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    lm_studio: LMStudioConfig = Field(default_factory=LMStudioConfig)
    llm_backend: str = "ollama"  # "ollama" or "lm_studio"
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    dry_run: bool = True
    scan_limit: Optional[int] = None  # None → scan all messages
    max_workers: Optional[int] = None # Manually specify workers


def build_imap_config(
    provider: Provider,
    username: str,
    password: str,
    host: Optional[str] = None,
    port: Optional[int] = None,
    use_ssl: Optional[bool] = None,
) -> IMAPConfig:
    """Construct an :class:`IMAPConfig` using provider defaults plus overrides."""
    defaults = PROVIDER_DEFAULTS[provider].copy()
    if host is not None:
        defaults["host"] = host
    if port is not None:
        defaults["port"] = port
    if use_ssl is not None:
        defaults["use_ssl"] = use_ssl
    return IMAPConfig(username=username, password=password, **defaults)
