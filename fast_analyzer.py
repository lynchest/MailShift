"""
fast_analyzer.py – Fast heuristic-based email analysis.
"""

from __future__ import annotations

from config import WHITELIST_PATTERN, JUNK_PATTERN

from models import MailMeta, ScanResult


def fast_analyze(meta: MailMeta) -> ScanResult:
    """
    Tier-1 heuristic analysis using pre-compiled regex patterns.

    Returns ScanResult with decision='SIL' if the message contains junk keywords
    and no whitelist keywords, 'TUT' otherwise.
    """
    text = f"{meta.subject} {meta.sender} {meta.body_preview}"

    if WHITELIST_PATTERN:
        match = WHITELIST_PATTERN.search(text)
        if match:
            return ScanResult(mail=meta, decision="TUT", reason=f"whitelist:{match.group()}")

    if JUNK_PATTERN:
        match = JUNK_PATTERN.search(text)
        if match:
            return ScanResult(mail=meta, decision="SIL", reason=f"heuristic:{match.group()}")

    return ScanResult(mail=meta, decision="TUT", reason="no match")
