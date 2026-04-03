"""
config.py | Pydantic-based configuration models for MailShift.
"""

from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from ..utils.paths import get_path


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
    Provider.GMAIL:  {"host": "imap.gmail.com", "port": 993, "use_ssl": True},
    Provider.PROTON: {"host": "127.0.0.1", "port": 1143, "use_ssl": False},
    Provider.CUSTOM: {"host": "", "port": 993, "use_ssl": True},
}

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

# ---------------------------------------------------------------------------
# Heuristic keyword lists & Pattern Management
# ---------------------------------------------------------------------------

class KeywordManager:
    """Manages dynamic loading, updating, and compiling of heuristic keywords."""
    
    def __init__(self):
        self.whitelist: List[str] = []
        self.blacklist_dict: Dict[str, List[str]] = {}
        
        # Compiled patterns
        self.whitelist_pattern: Optional[re.Pattern] = None
        self.junk_pattern: Optional[re.Pattern] = None
        
        # Fast lookup maps
        self.blacklist_category_map: Dict[str, str] = {}
        self.junk_keywords_flat: List[str] = []
        
        self.reload()

    def _load_json(self, filename: str, default: Any) -> Any:
        path = get_path(filename)
        if not path.exists():
            return default
        with open(path, encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return default

    def _save_json(self, filename: str, data: Any) -> None:
        path = get_path(filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _infer_category(self, keyword: str) -> str:
        key = keyword.lower()
        if any(t in key for t in ("newsletter", "bülten", "bulten", "digest", "weekly", "daily", "substack", "mailchimp")):
            return "newsletter"
        if any(t in key for t in ("unsubscribe", "abonelik", "aboneligi", "aboneliği", "list-unsubscribe", "opt out", "opt-out", "üyelik", "uyelik", "subscription", "listeden çık", "listeden cik", "preferences", "tercih")):
            return "subscription"
        if any(t in key for t in ("discount", "indirim", "kampanya", "campaign", "offer", "fırsat", "firsat", "sale", "coupon", "kupon", "free shipping", "ücretsiz kargo", "black friday", "deal", "promo", "promotion", "flash sale", "special offer", "sepet", "cart")):
            return "promotion"
        return "uncategorized"

    def reload(self) -> None:
        """Reloads keywords from disk and recompiles regex patterns."""
        self.whitelist = self._load_json("whitelist.json", [])
        
        raw_blacklist = self._load_json("blacklist.json", {"uncategorized": []})
        
        # Otomatik Format Göçü (List -> Dict)
        if isinstance(raw_blacklist, list):
            self.blacklist_dict = {}
            for item in raw_blacklist:
                if isinstance(item, str):
                    cat = self._infer_category(item)
                    self.blacklist_dict.setdefault(cat, []).append(item)
            self._save_json("blacklist.json", self.blacklist_dict)
        else:
            self.blacklist_dict = raw_blacklist

        # Yassılaştırma (Flatten) ve Haritalama (Mapping)
        self.junk_keywords_flat = []
        self.blacklist_category_map = {}
        
        for cat, items in self.blacklist_dict.items():
            if not isinstance(items, list): continue
            for word in items:
                if isinstance(word, str):
                    clean_word = word.strip().lower()
                    self.junk_keywords_flat.append(clean_word)
                    self.blacklist_category_map[clean_word] = cat.strip().lower()

        # Regex Derleme
        wl_escaped = [re.escape(k.lower()) for k in self.whitelist]
        junk_escaped = [re.escape(k) for k in self.junk_keywords_flat]
        
        self.whitelist_pattern = re.compile('|'.join(wl_escaped), re.IGNORECASE) if wl_escaped else None
        self.junk_pattern = re.compile('|'.join(junk_escaped), re.IGNORECASE) if junk_escaped else None

    # --- Public API for Keywords ---

    def add_whitelist(self, word: str) -> bool:
        if word not in self.whitelist:
            self.whitelist.append(word)
            self._save_json("whitelist.json", self.whitelist)
            self.reload()
            return True
        return False

    def remove_whitelist(self, word: str) -> bool:
        if word in self.whitelist:
            self.whitelist.remove(word)
            self._save_json("whitelist.json", self.whitelist)
            self.reload()
            return True
        return False

    def add_blacklist(self, word: str) -> bool:
        normalized = word.strip().lower()
        if normalized in self.blacklist_category_map:
            return False
            
        target_category = self._infer_category(normalized)
        self.blacklist_dict.setdefault(target_category, []).append(word)
        self._save_json("blacklist.json", self.blacklist_dict)
        self.reload()
        return True

    def remove_blacklist(self, word: str) -> bool:
        target = word.strip().lower()
        removed = False
        
        for category, items in self.blacklist_dict.items():
            if not isinstance(items, list): continue
            original_len = len(items)
            self.blacklist_dict[category] = [k for k in items if not (isinstance(k, str) and k.strip().lower() == target)]
            if len(self.blacklist_dict[category]) < original_len:
                removed = True

        if removed:
            self._save_json("blacklist.json", self.blacklist_dict)
            self.reload()
        return removed

    def get_category_for_match(self, matched_token: str) -> str:
        key = (matched_token or "").strip().lower()
        return self.blacklist_category_map.get(key, "uncategorized")

# Modül düzeyinde tekil instance (Singleton) oluştur
keyword_manager = KeywordManager()

# Geriye dönük uyumluluk için aracı fonksiyonlar (Diğer dosyalar bozulmasın diye)
def add_to_whitelist(word: str) -> bool: return keyword_manager.add_whitelist(word)
def remove_from_whitelist(word: str) -> bool: return keyword_manager.remove_whitelist(word)
def add_to_blacklist(word: str) -> bool: return keyword_manager.add_blacklist(word)
def remove_from_blacklist(word: str) -> bool: return keyword_manager.remove_blacklist(word)
def list_keywords() -> tuple[list[str], list[str]]: return keyword_manager.whitelist, keyword_manager.junk_keywords_flat
def get_blacklist_category_for_match(matched_token: str) -> str: return keyword_manager.get_category_for_match(matched_token)

# ---------------------------------------------------------------------------
# LLM & App Configurations
# ---------------------------------------------------------------------------

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
    model: str = "gemma-4-26b-a4b"
    timeout: int = 60
    max_body_chars: int = 250
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    use_think: bool = False

    model_config = ConfigDict(frozen=True)


class RateLimitConfig(BaseModel):
    """Rate limiting and retry settings for IMAP operations."""
    fetch_chunk_size: int = 100   # UIDs per IMAP fetch request
    delete_chunk_size: int = 100  # UIDs per IMAP store/copy request
    chunk_delay: float = 0.1      # 100 ms between chunks by default
    max_retries: int = 3          # Number of retry attempts per chunk
    retry_backoff: float = 2.0    # Exponential back-off multiplier (s, s*2, s*4 …)
    connect_timeout: int = 30     # Connection timeout (seconds) | applied to the underlying socket
    db_batch_size: int = 500      # Database batch-commit size

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
    scan_limit: Optional[int] = None
    since: Optional[str] = None
    before: Optional[str] = None
    max_workers: Optional[int] = None


def build_imap_config(
    provider: Provider,
    username: str,
    password: str,
    host: Optional[str] = None,
    port: Optional[int] = None,
    use_ssl: Optional[bool] = None,
) -> IMAPConfig:
    """Construct an IMAPConfig using provider defaults plus overrides."""
    defaults = PROVIDER_DEFAULTS[provider].copy()
    if host is not None:
        defaults["host"] = host
    if port is not None:
        defaults["port"] = port
    if use_ssl is not None:
        defaults["use_ssl"] = use_ssl
    return IMAPConfig(username=username, password=password, **defaults)
