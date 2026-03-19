"""

config.py | Pydantic-based configuration models for MailShift.

"""



from __future__ import annotations



from enum import Enum

from typing import Optional, Any



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

from ..utils.paths import get_path





def _load_keywords(filename: str) -> Any:

    path = get_path(filename)

    with open(path, encoding="utf-8") as f:

        return json.load(f)





def _save_keywords(filename: str, keywords: Any) -> None:

    path = get_path(filename)

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

    if isinstance(keywords, dict):

        normalized = word.strip().lower()

        flat_existing = {
            str(k).strip().lower()
            for items in keywords.values()
            if isinstance(items, list)
            for k in items
            if isinstance(k, str)
        }

        if normalized in flat_existing:

            return False

        target_category = "uncategorized"

        if target_category not in keywords or not isinstance(keywords[target_category], list):

            keywords[target_category] = []

        keywords[target_category].append(word)

        _save_keywords("blacklist.json", keywords)

        return True

    if not isinstance(keywords, list):

        return False

    if word not in keywords:

        keywords.append(word)

        _save_keywords("blacklist.json", keywords)

        return True

    return False





def remove_from_blacklist(word: str) -> bool:

    keywords = _load_keywords("blacklist.json")

    if isinstance(keywords, dict):

        removed = False

        target = word.strip().lower()

        for category, items in keywords.items():

            if not isinstance(items, list):

                continue

            before = len(items)

            keywords[category] = [k for k in items if not (isinstance(k, str) and k.strip().lower() == target)]

            if len(keywords[category]) < before:

                removed = True

        if removed:

            _save_keywords("blacklist.json", keywords)

        return removed

    if not isinstance(keywords, list):

        return False

    if word in keywords:

        keywords.remove(word)

        _save_keywords("blacklist.json", keywords)

        return True

    return False





def list_keywords() -> tuple[list[str], list[str]]:

    whitelist = _load_keywords("whitelist.json")

    blacklist = _flatten_blacklist_keywords(_load_keywords("blacklist.json"))

    return whitelist, blacklist





def _flatten_blacklist_keywords(raw_blacklist: Any) -> list[str]:

    if isinstance(raw_blacklist, list):

        return [k for k in raw_blacklist if isinstance(k, str)]

    if isinstance(raw_blacklist, dict):

        flattened: list[str] = []

        for items in raw_blacklist.values():

            if isinstance(items, list):

                flattened.extend(k for k in items if isinstance(k, str))

        return flattened

    return []


def _build_blacklist_category_map(raw_blacklist: Any) -> dict[str, str]:

    def _infer_category(keyword: str) -> str:

        key = keyword.lower()

        newsletter_tokens = (
            "newsletter", "bülten", "bulten", "digest", "weekly", "daily", "substack", "mailchimp",
        )

        subscription_tokens = (
            "unsubscribe", "abonelik", "aboneligi", "aboneliği", "list-unsubscribe", "opt out", "opt-out",
            "üyelik", "uyelik", "subscription", "listeden çık", "listeden cik", "preferences", "tercih",
        )

        promotion_tokens = (
            "discount", "indirim", "kampanya", "campaign", "offer", "fırsat", "firsat", "sale", "coupon",
            "kupon", "free shipping", "ücretsiz kargo", "black friday", "deal", "promo", "promotion",
            "flash sale", "special offer", "sepet", "cart",
        )

        if any(token in key for token in newsletter_tokens):

            return "newsletter"

        if any(token in key for token in subscription_tokens):

            return "subscription"

        if any(token in key for token in promotion_tokens):

            return "promotion"

        return "uncategorized"

    mapping: dict[str, str] = {}

    if isinstance(raw_blacklist, dict):

        for category, items in raw_blacklist.items():

            cat = str(category).strip().lower() or "uncategorized"

            if not isinstance(items, list):

                continue

            for item in items:

                if isinstance(item, str):

                    mapping[item.lower()] = cat

        return mapping

    if isinstance(raw_blacklist, list):

        for item in raw_blacklist:

            if isinstance(item, str):

                mapping[item.lower()] = _infer_category(item)

    return mapping


RAW_BLACKLIST_KEYWORDS: Any = _load_keywords("blacklist.json")

JUNK_KEYWORDS: list[str] = _flatten_blacklist_keywords(RAW_BLACKLIST_KEYWORDS)

BLACKLIST_CATEGORY_MAP: dict[str, str] = _build_blacklist_category_map(RAW_BLACKLIST_KEYWORDS)

WHITELIST_KEYWORDS: list[str] = _load_keywords("whitelist.json")



import re



JUNK_KEYWORDS_LOWER: list[str] = [k.lower() for k in JUNK_KEYWORDS]

WHITELIST_KEYWORDS_LOWER: list[str] = [k.lower() for k in WHITELIST_KEYWORDS]



_JUNK_ESCAPED = [re.escape(k) for k in JUNK_KEYWORDS_LOWER]

_WHITELIST_ESCAPED = [re.escape(k) for k in WHITELIST_KEYWORDS_LOWER]



JUNK_PATTERN = re.compile('|'.join(_JUNK_ESCAPED), re.IGNORECASE) if _JUNK_ESCAPED else None

WHITELIST_PATTERN = re.compile('|'.join(_WHITELIST_ESCAPED), re.IGNORECASE) if _WHITELIST_ESCAPED else None


def get_blacklist_category_for_match(matched_token: str) -> str:

    key = (matched_token or "").strip().lower()

    if not key:

        return "uncategorized"

    return BLACKLIST_CATEGORY_MAP.get(key, "uncategorized")

DEFAULT_SYSTEM_PROMPT = """
MailShift için e-posta sınıflandırması yap.

Yalnızca geçerli JSON döndür:
{"decision":"SIL|TUT","reason":"kısa"}

Karar kuralları:
- SIL: Pazarlama/satış, kampanya/indirim, sepet hatırlatma, bülten/digest, sadakat-puan/VIP, gamification, genel kurumsal duyurular.
- TUT: Kişisel yazışma, fatura/dekont, banka/ödeme, şifre sıfırlama/OTP, kargo/teslimat, resmi vergi-hukuki bildirim, abonelik iptali/ücretli yenileme uyarısı.

Çakışma kuralı:
- Mesaj hem pazarlama hem operasyonel görünüyorsa güvenlik/ödeme/fatura içeriği varsa TUT, yoksa SIL.

Yanıt kuralları:
- Decision sadece SIL veya TUT olmalı.
- Reason 2-5 kelime olmalı.
- JSON dışında hiçbir metin yazma.

"""





class OllamaConfig(BaseModel):

    """Settings for the local Ollama LLM endpoint."""



    base_url: str = "http://localhost:11434"

    model: str = "qwen3.5:0.8B"

    timeout: int = 60

    max_body_chars: int = 250



    system_prompt: str = DEFAULT_SYSTEM_PROMPT

    use_think: bool = False



    model_config = ConfigDict(frozen=True)





class LMStudioConfig(BaseModel):

    """Settings for the LM Studio OpenAI-compatible endpoint."""



    base_url: str = "http://localhost:1234"

    model: str = ""

    timeout: int = 60

    max_body_chars: int = 250



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



    # Connection timeout (seconds) | applied to the underlying socket

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

    scan_limit: Optional[int] = None  # None â†’ scan all messages

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

