"""
fast_analyzer.py – Fast heuristic-based email analysis.
"""

from __future__ import annotations

import re

from config import WHITELIST_PATTERN, JUNK_PATTERN

from models import MailMeta, ScanResult


# Always keep these transactional/security classes in fast mode,
# even if sender includes junk-like tokens such as no-reply.
_ALWAYS_KEEP_PATTERN = re.compile(
    r"""
    (
        # Premium lifecycle notices (expiry/end of trial)
        \bpremium\b.{0,80}(bitiyor|bitecek|ending|expires?|expiring|sona\s+eriyor|deneme\s+s[üu]reniz\s+bitiyor)
        |
        # Verification / OTP codes
        \b(otp|verification\s+code|verify\s+code|do[ğg]rulama\s+kodu|onay\s+kodu)\b
        |
        # Google Drive / cloud storage quota fullness notices
        (google\s*drive|gdrive|onedrive|icloud|drive)\b.{0,120}(dol[uı]|dolmak\s+[üu]zere|storage|depolama|quota|space\s+(?:is\s+)?(?:almost\s+)?full|kota)
        |
        # Phishing heuristics (force TUT to avoid SILing real mails by mistake)
        \b(urgent|account\s+suspended|verify\s+your\s+identity|tebrikler.{0,30}kazand[ıi]n[ıi]z|çekiliş|şifre(nizi)?\s+sıfırlayın|kart\s+bilgileri(nizi)?\s+güncelleyin)\b
        |
        # Award/Gift only if it looks like a winning notice (phishing risk)
        \b(ödül|hediye).{0,20}(kazand[ıi]n[ıi]z|hesab[ıi]n[ıi]za\s+tan[ıi]mland[ıi])\b
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _normalize(text: str) -> str:
    """Lowercase with explicit Turkish dotted-İ mapping."""
    return text.replace("İ", "i").lower()


def fast_analyze(meta: MailMeta) -> ScanResult:
    """
    Tier-1 heuristic analysis using pre-compiled regex patterns.

    Returns ScanResult with decision='SIL' if the message contains junk keywords
    and no whitelist keywords, 'TUT' otherwise.
    """
    if meta.has_attachment:
        return ScanResult(mail=meta, decision="TUT", reason="has_attachment")

    # full_text includes sender for whitelist and safety-guard; content_text
    # excludes it for blacklist to prevent false positives from legitimate
    # automated senders (e.g. no-reply@github.com matching the "no-reply" rule).
    full_text = _normalize(f"{meta.subject} {meta.sender} {meta.body_preview}")
    content_text = _normalize(f"{meta.subject} {meta.body_preview}")

    if WHITELIST_PATTERN:
        match = WHITELIST_PATTERN.search(full_text)
        if match:
            return ScanResult(mail=meta, decision="TUT", reason=f"whitelist:{match.group()}")

    match = _ALWAYS_KEEP_PATTERN.search(full_text)
    if match:
        return ScanResult(mail=meta, decision="TUT", reason=f"safe-guard:{match.group()}")

    if JUNK_PATTERN:
        match = JUNK_PATTERN.search(content_text)
        if match:
            return ScanResult(mail=meta, decision="SIL", reason=f"heuristic:{match.group()}")

    return ScanResult(mail=meta, decision="TUT", reason="no match")
