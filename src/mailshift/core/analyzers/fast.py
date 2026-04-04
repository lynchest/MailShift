"""
fast_analyzer.py | Fast heuristic-based email analysis.
"""

from __future__ import annotations

import re

# Statik patternler yerine Singleton KeywordManager'ı çağırıyoruz.
# Böylece çalışma zamanında eklenen kelimeler anında taranmaya başlar.
from ...config.config import keyword_manager
from ...models.models import MailMeta, ScanResult

# Backwards-compatible module-level pattern names used by tests
# Tests patch `mailshift.core.analyzers.fast.WHITELIST_PATTERN` / `JUNK_PATTERN`.
WHITELIST_PATTERN = None
JUNK_PATTERN = None

# re.IGNORECASE flag'i kaldırıldı (metin zaten normalize ediliyor).
# Yakalama gerektirmeyen (Non-capturing) gruplar (?:...) kullanılarak regex motorunun bellek tahsisi azaltıldı.
_ALWAYS_KEEP_PATTERN = re.compile(
    r"""
    (?:
        # Premium lifecycle notices (expiry/end of trial)
        \bpremium\b.{0,80}(?:bitiyor|bitecek|ending|expires?|expiring|sona\s+eriyor|deneme\s+s[üu]reniz\s+bitiyor)
        |
        # Verification / OTP codes
        \b(?:otp|verification\s+code|verify\s+code|do[ğg]rulama\s+kodu|onay\s+kodu)\b
        |
        # Google Drive / cloud storage / service quota fullness notices
        (?:google\s*drive|gdrive|onedrive|icloud|drive|nextdns)\b.{0,120}(?:dol[uı]|dolmak\s+[üu]zere|storage|depolama|quota|space\s+(?:is\s+)?(?:almost\s+)?full|kota|limit|exceeded)
        |
        # Phishing heuristics (force TUT to avoid SILing real mails by mistake)
        \b(?:urgent|account\s+suspended|verify\s+your\s+identity|tebrikler.{0,30}kazand[ıi]n[ıi]z|çekiliş|şifre(?:nizi)?\s+sıfırlayın|kart\s+bilgileri(?:nizi)?\s+güncelleyin)\b
        |
        # Award/Gift only if it looks like a winning notice (phishing risk)
        \b(?:ödül|hediye).{0,20}(?:kazand[ıi]n[ıi]z|hesab[ıi]n[ıi]za\s+tan[ıi]mland[ıi])\b
    )
    """,
    re.VERBOSE,
)


def _normalize(text: str | None) -> str:
    """Lowercase with explicit Turkish dotted-İ and dotless-I mapping."""
    if not text:
        return ""
    # "I" harfinin "i" yerine "ı" olmasına ve "İ" harfinin "i" olmasına dikkat ediyoruz.
    return text.replace("İ", "i").replace("I", "ı").lower()


def extract_fast_category(reason: str) -> str:
    """Extracts the category from a reason string formatted as 'heuristic:category:token'"""
    # Non-heuristic reasons don't have a category (return empty string)
    if not reason.startswith("heuristic:"):
        return ""
    # Expecting format: heuristic:category:token
    parts = reason.split(":", 2)
    if len(parts) == 3:
        return parts[1]
    # No category provided
    return "uncategorized"


def fast_analyze(meta: MailMeta) -> ScanResult:
    """
    Tier-1 heuristic analysis using pre-compiled regex patterns.
    Optimized for short-circuit evaluation and safe attachment handling.
    """
    
    # 1. Eklenti (Attachment) Koruması (En hızlı çıkış noktası)
    if meta.has_attachment:
        return ScanResult(mail=meta, decision="TUT", reason="has_attachment")

    # 2. Öncelikli Normalizasyon (Header Seviyesi)
    # Bellek tahsisini geciktirmek için önce sadece başlıkları işliyoruz.
    subject_text = _normalize(meta.subject)
    sender_text = _normalize(meta.sender)
    header_text = f"{subject_text} {sender_text}"

    # Referansı yerel değişkene alıyoruz ki lookup hızı artsın
    # Prefer module-level patched patterns (tests) and fall back to keyword_manager.
    wl_pattern = globals().get("WHITELIST_PATTERN")
    if wl_pattern is None:
        wl_pattern = keyword_manager.whitelist_pattern
    junk_pattern = globals().get("JUNK_PATTERN")
    if junk_pattern is None:
        junk_pattern = keyword_manager.junk_pattern

    # 3. Whitelist Kontrolü (Önce sadece Header'da)
    if wl_pattern:
        match = wl_pattern.search(header_text)
        if match:
            return ScanResult(mail=meta, decision="TUT", reason=f"whitelist:{match.group()}")

    # 4. Body Normalizasyonu (Header'dan whitelist geçilemezse çalışır)
    body_text = _normalize(meta.body_preview)
    
    # Whitelist fallback (Body Kontrolü)
    if wl_pattern:
        match = wl_pattern.search(body_text)
        if match:
            return ScanResult(mail=meta, decision="TUT", reason=f"whitelist:{match.group()}")

    full_text = f"{header_text} {body_text}"

    # 5. Phishing ve Safe-Guard Kontrolü
    safe_match = _ALWAYS_KEEP_PATTERN.search(full_text)
    if safe_match:
        return ScanResult(mail=meta, decision="TUT", reason=f"safe-guard:{safe_match.group()}")

    # 6. Junk / Spam Kontrolü (Gönderici Hariç - Yalnızca Konu ve İçerik)
    if junk_pattern:
        content_text = f"{subject_text} {body_text}"
        match = junk_pattern.search(content_text)
        if match:
            matched_token = match.group()
            # Dinamik kategori haritasından token'ın kategorisini çekiyoruz
            category = keyword_manager.get_category_for_match(matched_token)
            return ScanResult(mail=meta, decision="SIL", reason=f"heuristic:{category}:{matched_token}")

    # 7. Varsayılan (Eşleşme Yok)
    return ScanResult(mail=meta, decision="TUT", reason="no match")