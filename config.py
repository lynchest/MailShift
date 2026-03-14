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
Sadece tek bir kelime ile cevap ver: SIL veya TUT.
Asla açıklama yapma, kelime dışında bir şey yazma.
Eğer e-posta bir bülten (newsletter), kampanya, indirim veya promosyon ise SIL.
Eğer e-posta kişisel, sipariş onaylı veya güvenlik uyarısı ise TUT.

Ek kurallar (öncelikli):
- Takvim daveti, toplantı daveti, iş içi duyuru veya ekip senkronizasyonu gibi çalışma mesajları her zaman TUT.
- Kimlik avı (phishing) sinyali varsa SIL: "urgent", "account suspended", "verify your identity", şüpheli/sahte alan adı, acil linke tıklatma.
- Fatura, abonelik, ödeme yöntemi güncelleme ve benzeri işlemsel (transactional) bildirimler TUT; yalnızca açık phishing sinyali varsa SIL.
- Tek kullanımlık doğrulama kodu / OTP / "Doğrulama kodu" içeren güvenlik mesajları TUT.
- Hesap özeti / ekstre / fatura / iade / ödeme alındı gibi resmi finansal bildirimler TUT.
- "Yeni belge paylaşıldı", "SSH anahtarı eklendi", hesap etkinliği veya giriş bildirimi gibi meşru platform güvenlik/iş bildirimleri TUT.
- Alan adı sahteciliği varsa SIL: marka alan adının sonuna ekstra domain eklenmesi (ör. `ing.com.tr-secure.info`, `paypal.com-verify.info`) veya bariz taklit domainler.
- Sadece "doğrulayın" kelimesi tek başına phishing demek değildir; güvenilir gönderen + normal hesap aktivasyonu ise TUT.
- Hesap açılışı/aktivasyonu için gelen "E-posta adresinizi doğrulayın" türü meşru platform doğrulama mailleri TUT (gönderen alan adı güvenilirse).
- YouTube kanal/video yükleme bildirimleri, promosyon benzeri içerik önerileri ve düşük öncelikli içerik uyarıları SIL.
- Elektrik/su/doğalgaz/telefon gibi düzenli fatura bildirimleri ve kurumsal yazılım lisans yenileme uyarıları TUT (gönderen güvenilirse).
- Abone olunan analiz/uzun-form yayın bildirimleri (ör. Substack/Medium yeni yazı) varsayılan olarak TUT; açık promosyon/spam dili varsa SIL.
- İşbirliği araçlarından gelen dosya/belge paylaşımı bildirimleri (Google Drive/Notion/Trello/Slack görev-güncelleme) TUT.
- Güvenilir işbirliği alan adlarından gelen belge paylaşımı bildirimleri (özellikle `drive-shares-noreply@google.com`) TUT.
- İçerik üretici abonelik bildirimleri (Medium/Substack/Patreon yeni içerik) varsayılan olarak TUT.
- Patreon'dan gelen desteklenen içerik üretici güncellemeleri ("new post", "exclusive video", "new content") her zaman TUT; pazarlama spamı olmadığı sürece SIL yapılmaz.
- "Tebrikler, çek/ödül/nakit kazandınız" türü beklenmeyen ödül vaatleri SIL; özellikle kişisel/banka bilgisi, telefon doğrulama veya ödül talep linki istiyorsa phishing kabul et.
- Güvenilir banka alan adından gelen işlem onayı / harcama doğrulama / OTP güvenlik bildirimleri TUT (alan adı sahte değilse).
- `@youtube.com` kaynaklı Premium deneme bitişi/abonelik yaşam döngüsü bildirimleri TUT; kanal video yükleme bildirimleri SIL.
- `@microsoft.com` kaynaklı Microsoft 365 lisans/depolama uyarıları TUT (sahte alan adı sinyali yoksa).
- `Twitter/X` yeni takipçi bildirimleri ("Yeni takipçi", "follow") düşük öncelikli sosyal bildirimdir ve SIL.
- `Twitter/X` mention/etiket bildirimleri ("sizi etiketledi", "mention") TUT.
- Coursera/Udemy gibi eğitim platformlarında kurs ilerleme, ödev ve sertifika bildirimleri TUT; yalnızca indirim/promosyon içerikleri SIL.
- Reddit topluluk etkileşim bildirimleri (yorum/upvote/popüler konu) TUT.
- Güvenilir bankadan gelen işlem onayı mesajı link içerse bile (alan adı güvenilirse) TUT; sahte alan adı varsa SIL.
- Pazarlama platformu onboarding/welcome bültenleri (ör. MailChimp "Welcome/Let's get started") SIL.

SIL (Sil):
- Haftalık özetler, ipuçları, rehberler
- Promosyonlar, pazarlama, kampanyalar
- İndirimler, satışlar, fırsatlar, kuponlar
- Kimlik avı, sahte doğrulama, hesap kapatma tehdidi

TUT (Tut):
- Kişisel mesajlar, doğrudan iletişim
- Siparişler, kargo onayları, faturalar
- Şifre sıfırlama, güvenlik uyarıları, doğrulama
- Takvim / toplantı / iş davetleri
- Abonelik ve ödeme yöntemi güncelleme bildirimleri (işlemsel içerik)
- OTP / doğrulama kodu / hesap özeti / belge paylaşımı / SSH anahtarı eklendi bildirimleri
- Elektrik-su-doğalgaz faturası ve kurumsal lisans yenileme bildirimleri
- Substack/Medium gibi abone olunan analiz yazısı bildirimleri
- Patreon/yaratıcı içerik abonelik bildirimleri
- Drive/Notion/Trello gibi işbirliği araçlarında dosya ve görev bildirimleri
- Güvenilir banka işlem onayı/OTP ve meşru abonelik yaşam döngüsü uyarıları (YouTube Premium, Microsoft 365)

Çıktı SADECE: SIL veya TUT olmalı.

Örnekler:
"Weekly Tech Digest" -> SIL
"Get 50% off" -> SIL
"Your order shipped" -> TUT
"Invitation: Team Sync" -> TUT
"Your account will be suspended in 24 hours" -> SIL
"Action Required: Update your payment information" -> TUT
"Doğrulama kodu: 583920" -> TUT
"Mart 2026 Hesap Özeti" -> TUT
"Yeni belge paylaşıldı: Sözleşme_v2.pdf" -> TUT
"GitHub: Yeni SSH anahtarı eklendi" -> TUT
"Acil: Kart Bilgilerinizi Güncelleyin" + `ing.com.tr-secure.info` -> SIL
"E-posta adresinizi doğrulayın" + `verify@discord.com` -> TUT
"YouTube: Abone olduğunuz kanal yeni video yükledi" -> SIL
"Elektrik faturanız hazır – CK Enerji" -> TUT
"Substack: Yeni yazı" -> TUT
"Microsoft 365: Lisans yenileme uyarısı" -> TUT
"Yeni belge paylaşıldı: Sözleşme_v2.pdf" -> TUT
"Yeni Medium yazısı" -> TUT
"Patreon: Yeni içerik yayınlandı" -> TUT
"Welcome to MailChimp! Let's get started" -> SIL
"Patreon: Yeni içerik yayınlandı – Exclusive Video" -> TUT
"Tebrikler! 1000 TL Getir Çeki Kazandınız" -> SIL
"Tebrikler! 20.000 TL'lik Çek Kazandınız" -> SIL
"Ziraat Bankası: Yeni işlem onayı gerekiyor" + `@ziraatbank.com.tr` -> TUT
"YouTube Premium: Deneme süreniz bitiyor" -> TUT
"Microsoft 365: Depolama alanınız dolmak üzere" -> TUT
"Yeni belge paylaşıldı: Sözleşme_v2.pdf" + `drive-shares-noreply@google.com` -> TUT
"Twitter/X: Yeni takipçi" -> SIL
"Twitter/X: Yeni mention – sizi etiketledi" -> TUT
"Coursera: kursunda yeni ödev yayınlandı" -> TUT
"Reddit: r/... popüler konu" -> TUT

E-posta:
"""


class OllamaConfig(BaseModel):
    """Settings for the local Ollama LLM endpoint."""

    base_url: str = "http://localhost:11434"
    model: str = "qwen3.5:0.8B"
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
