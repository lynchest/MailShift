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
You are an email classifier. Classify each email as either SIL or TUT.

SIL = Delete: newsletters, promotions, marketing, mass-sent announcements, subscription updates, discount, indirim, sale, offer, % off, free shipping, limited time, bonus, gift card, coupon, deal, flash sale, black friday, cyber monday, campaign

TUT = Keep: personal messages, transactional (orders, shipping), meeting requests, anything requiring direct attention

ALWAYS classify as TUT (never SIL), regardless of wording:
- Account changes, password resets, security alerts
- Data access or transfer requests
- Login notifications, verification codes
- Any email implying an action was taken on the recipient's account

Rules:
- If the email contains ANY discount, promotion, sale, offer, coupon, bonus, free, % off, or similar marketing language → SIL
- Output ONLY the label: SIL or TUT
- No explanation, no punctuation, nothing else

Examples:
"Get 50% off all items! Shop now." → SIL
"Your order #12345 has shipped." → TUT
"Weekly Newsletter – Top 10 recipes" → SIL
"Hi John, can we meet tomorrow at 3pm?" → TUT
"Action required: verify your account" → TUT
"You are subscribed to Product Updates" → SIL
"Meeting reminder: standup at 10 AM" → TUT
"Your Apple ID information was updated." → TUT
"A request was made to transfer your Google data." → TUT
"Today only: 40% discount on all products" → SIL
"Free shipping on orders over $50" → SIL
"Your exclusive offer awaits - 25% off" → SIL

Email to classify:
"""


class OllamaConfig(BaseModel):
    """Settings for the local Ollama LLM endpoint."""

    base_url: str = "http://localhost:11434"
    model: str = "qwen3.5:2B"
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
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    dry_run: bool = True
    scan_limit: Optional[int] = None  # None → scan all messages


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
