"""
unsubscribe.py – Utilities for handling List-Unsubscribe actions.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..utils.logger import log


@dataclass
class UnsubscribeEntry:
    """Represents a unique unsubscribe target (one per distinct URL)."""

    sender: str
    unsubscribe_url: str
    mail_count: int = 1


def build_unsubscribe_entries(results) -> list[UnsubscribeEntry]:
    """
    Deduplicate ScanResults by unsubscribe URL and return a list of
    UnsubscribeEntry objects sorted by mail_count descending.
    """
    seen: dict[str, UnsubscribeEntry] = {}
    for r in results:
        url = r.mail.unsubscribe_url
        if not url:
            continue
        if url in seen:
            seen[url].mail_count += 1
        else:
            seen[url] = UnsubscribeEntry(
                sender=r.mail.sender,
                unsubscribe_url=url,
                mail_count=1,
            )
    return sorted(seen.values(), key=lambda e: e.mail_count, reverse=True)


def perform_unsubscribe(url: str) -> tuple[bool, str]:
    """
    Send an unsubscribe request to *url*.

    Tries GET first; if the server returns 4xx/5xx, falls back to a
    RFC 8058 one-click POST (body: ``List-Unsubscribe=One-Click``).

    Returns ``(success, status_message)``.
    """
    headers = {"User-Agent": "Mozilla/5.0 (compatible; MailShift/1.0)"}

    # --- GET attempt ---
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            if 200 <= resp.status < 400:
                return True, f"GET {resp.status}"
    except urllib.error.HTTPError as exc:
        log.debug(f"Unsubscribe GET failed ({exc.code}), trying POST: {url}")
    except Exception as exc:
        log.debug(f"Unsubscribe GET error, trying POST: {exc}")

    # --- POST fallback (RFC 8058) ---
    try:
        post_data = b"List-Unsubscribe=One-Click"
        req = urllib.request.Request(
            url,
            data=post_data,
            headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 400, f"POST {resp.status}"
    except urllib.error.HTTPError as exc:
        return False, f"POST {exc.code}"
    except Exception as exc:
        return False, str(exc)


def export_unsubscribe_links(entries: list[UnsubscribeEntry], output_path: str) -> None:
    """
    Write unsubscribe entries to *output_path*.

    Supports ``.json`` and ``.txt`` extensions.
    """
    path = Path(output_path)
    suffix = path.suffix.lower()

    if suffix == ".json":
        data = {
            "exported_at": datetime.now().isoformat(),
            "total": len(entries),
            "entries": [
                {
                    "sender": e.sender,
                    "mail_count": e.mail_count,
                    "unsubscribe_url": e.unsubscribe_url,
                }
                for e in entries
            ],
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        # Plain text: one URL per line with sender info
        lines = [
            f"# MailShift – Unsubscribe Links",
            f"# Exported: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"# Total: {len(entries)}",
            "",
        ]
        for e in entries:
            lines.append(f"# {e.sender}  ({e.mail_count} mail)")
            lines.append(e.unsubscribe_url)
            lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")
