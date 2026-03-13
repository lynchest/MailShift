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


JUNK_KEYWORDS: list[str] = _load_keywords("blacklist.json")
WHITELIST_KEYWORDS: list[str] = _load_keywords("whitelist.json")

import re

JUNK_KEYWORDS_LOWER: list[str] = [k.lower() for k in JUNK_KEYWORDS]
WHITELIST_KEYWORDS_LOWER: list[str] = [k.lower() for k in WHITELIST_KEYWORDS]

_JUNK_ESCAPED = [re.escape(k) for k in JUNK_KEYWORDS_LOWER]
_WHITELIST_ESCAPED = [re.escape(k) for k in WHITELIST_KEYWORDS_LOWER]

JUNK_PATTERN = re.compile('|'.join(_JUNK_ESCAPED), re.IGNORECASE) if _JUNK_ESCAPED else None
WHITELIST_PATTERN = re.compile('|'.join(_WHITELIST_ESCAPED), re.IGNORECASE) if _WHITELIST_ESCAPED else None


class OllamaConfig(BaseModel):
    """Settings for the local Ollama LLM endpoint."""

    base_url: str = "http://localhost:11434"
    model: str = "qwen2.5:3b"
    timeout: int = 30
    max_body_chars: int = 500

    # System prompt for the LLM
    system_prompt: str = (
        "You are a mail filter. Analyze if this email is a generic newsletter/spam "
        "or a personal/important document. "
        "Output ONLY 'SIL' (Delete) or 'TUT' (Keep). No explanation."
    )

    model_config = ConfigDict(frozen=True)


class AppConfig(BaseModel):
    """Top-level application configuration."""

    provider: Provider
    mode: Mode
    imap: IMAPConfig
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    dry_run: bool = True
    max_workers: int = 8
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
