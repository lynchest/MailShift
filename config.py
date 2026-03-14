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
Sen bir e-posta temizleme asistanısın. Görevin, gelen kutusundaki gereksiz (junk/spam/marketing) e-postaları SIL (Sil) veya TUT (Tut) olarak sınıflandırmaktır.

JSON formatında cevap ver: {"decision": "SIL" | "TUT", "reason": "kısa açıklama"}

TUT (Tutulacaklar - Önemli):
1. Kişisel: Gerçek kişilerden gelen mesajlar ("Annenden", "Mehmet Bey'den").
2. İş/Kariyer: İş başvurusu güncellemeleri, mülakat davetleri, teklif mektupları.
3. Finans/Resmi: Banka ekstreleri, faturalar, vergi ödemeleri, resmi kurum (e-devlet, belediye su/elektrik) bildirimleri.
4. Güvenlik: Doğrulama kodları (OTP), şifre sıfırlama, güvenlik uyarıları.
5. İş Araçları: GitHub PR bildirimleri, Jira kritik hatalar, Slack direkt mesaj bildirimleri, Drive paylaşımları.
6. Ödemeli/Değerli İçerik: Substack/Medium gibi gerçekten takip ettiğin yazarların içerikleri.

SIL (Silinecekler - Gereksiz):
1. Pazarlama ve Promosyon: "İndirim", "Kampanya", "%50 fırsat", "Sadece sana özel", "VIP", "Son şans", "Kaçırma".
2. Haber Bültenleri (Newsletter): "Weekly Tech Digest", "Haftalık Bülten", "Önemli Gelişmeler" gibi genel toplu gönderimler.
3. Sosyal Medya/Platform Bildirimleri: "X kişisi yeni video yükledi", "Youtube: Abone olduğun kanal...", "Yeni takipçi", "Trendler", "Popüler konular".
4. Onboarding/Hoşgeldin: "MailShift'e Hoşgeldin", "Hadi Başlayalım", "Profilini Tamamla".
5. Otomatik Duyurular: İftar programları, genel etkinlik duyuruları, anketler.

KRİTİK KURAL: Eğer e-posta bir ürünü satmaya, bir siteye geri çağırmaya veya genel bir bilgi (digest) vermeye çalışıyorsa SIL kararını ver.

ÖRNEKLER:
- "Annenden: Market listesi" -> TUT
- "Başvurunuz alındı - Trendyol" -> TUT
- "Weekly Python Tips" -> SIL
- "Get 50% off - Nike" -> SIL
- "Yeni takipçiniz var" -> SIL
- "Doğrula kodu: 123456" -> TUT
- "Ramazan İftar Programı Duyurusu" -> SIL
- "Mailchimp: Hoşgeldiniz" -> SIL

E-posta şudur:
"""


class OllamaConfig(BaseModel):
    """Settings for the local Ollama LLM endpoint."""

    base_url: str = "http://localhost:11434"
    model: str = "qwen3.5:0.8B"
    timeout: int = 300
    max_body_chars: int = 500

    system_prompt: str = DEFAULT_SYSTEM_PROMPT

    model_config = ConfigDict(frozen=True)


class LMStudioConfig(BaseModel):
    """Settings for the LM Studio OpenAI-compatible endpoint."""

    base_url: str = "http://localhost:1234"
    model: str = ""
    timeout: int = 300
    max_body_chars: int = 500

    system_prompt: str = DEFAULT_SYSTEM_PROMPT

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
