"""
engine.py – Mail fetching, heuristic analysis, Ollama LLM analysis, and deletion.
"""

from __future__ import annotations

import email
import email.header
import imaplib
import json
import ssl
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from email.message import Message
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Callable, Iterator, Optional

import requests
from bs4 import BeautifulSoup

from models import MailMeta, ScanResult, ScanStats


class RateLimiter:
    """Simple token bucket rate limiter for Proton Mail API limits."""

    def __init__(self, max_per_minute: int) -> None:
        self.max_per_minute = max_per_minute
        self.interval = 60.0 / max_per_minute
        self.last_request_time = 0.0
        self.lock = Lock()

    def acquire(self) -> None:
        with self.lock:
            now = time.time()
            elapsed = now - self.last_request_time
            if elapsed < self.interval:
                time.sleep(self.interval - elapsed)
            self.last_request_time = time.time()
from config import (
    AppConfig,
    IMAPConfig,
    Mode,
    OllamaConfig,
)

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# IMAP helpers
# ---------------------------------------------------------------------------
# IMAP helpers
# ---------------------------------------------------------------------------
# IMAP helpers
# ---------------------------------------------------------------------------


def _connect(cfg: IMAPConfig) -> imaplib.IMAP4 | imaplib.IMAP4_SSL:
    """Open and authenticate an IMAP connection."""
    if cfg.use_ssl:
        ctx = ssl.create_default_context()
        conn = imaplib.IMAP4_SSL(cfg.host, cfg.port, ssl_context=ctx)
    else:
        conn = imaplib.IMAP4(cfg.host, cfg.port)
    conn.login(cfg.username, cfg.password.get_secret_value())
    return conn


def _decode_header_value(raw: str | bytes | None) -> str:
    """Decode an RFC-2047-encoded header value to a plain string."""
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    parts = email.header.decode_header(raw)
    decoded_parts: list[str] = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded_parts.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded_parts.append(str(part))
    return " ".join(decoded_parts)


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------

CACHE_FILE = Path("mails_cache.json")


def save_mails_cache(mails: list[MailMeta]) -> None:
    """Save fetched mails to cache file."""
    data = [asdict(mail) for mail in mails]
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)


def load_mails_cache() -> Optional[list[MailMeta]]:
    """Load mails from cache file if exists."""
    if not CACHE_FILE.exists():
        return None
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return [MailMeta(**d) for d in data]
    except Exception:
        return None


def clear_mails_cache() -> None:
    """Delete the cache file."""
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()


def _html_to_text(html: str) -> str:
    """Convert HTML content to plain text, preserving readable text."""
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(separator=" ", strip=True)


def _has_attachment(msg: Message) -> bool:
    """Check if the message has any attachments."""
    if msg.is_multipart():
        for part in msg.walk():
            cd = str(part.get("Content-Disposition", ""))
            if "attachment" in cd.lower():
                return True
    else:
        cd = str(msg.get("Content-Disposition", ""))
        if "attachment" in cd.lower():
            return True
    return False


def _extract_body_preview(msg: Message, max_chars: int = 500) -> str:
    """Extract the first *max_chars* characters of the plain-text body."""
    text_body = ""
    html_body = ""

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if "attachment" in cd:
                continue
            if ct == "text/plain" and not text_body:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    text_body = payload.decode(charset, errors="replace")
            elif ct == "text/html" and not html_body:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    html_body = payload.decode(charset, errors="replace")
    else:
        ct = msg.get_content_type()
        if ct == "text/plain":
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                text_body = payload.decode(charset, errors="replace")
        elif ct == "text/html":
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                html_body = payload.decode(charset, errors="replace")

    if text_body:
        return text_body[:max_chars]
    if html_body:
        return _html_to_text(html_body)[:max_chars]
    return ""


def _fetch_mail_meta(
    conn: imaplib.IMAP4 | imaplib.IMAP4_SSL,
    uid: str,
    fetch_body: bool = False,
    max_body_chars: int = 500,
) -> Optional[MailMeta]:
    """Fetch headers (and optionally body preview) for a single UID."""
    try:
        fetch_parts = "(RFC822.SIZE RFC822.HEADER)"
        if fetch_body:
            fetch_parts = "(RFC822.SIZE RFC822)"

        status, data = conn.uid("fetch", uid, fetch_parts)
        if status != "OK" or not data or data[0] is None:
            return None

        raw = data[0]
        if isinstance(raw, tuple):
            # data[0] is (b'... {size}', b'<raw bytes>')
            size_str = raw[0].decode()
            size_bytes = 0
            for token in size_str.split():
                if token.isdigit():
                    size_bytes = int(token)
                    break
            raw_bytes = raw[1]
        else:
            return None

        msg = email.message_from_bytes(raw_bytes)
        subject = _decode_header_value(msg.get("Subject"))
        sender = _decode_header_value(msg.get("From"))
        date = _decode_header_value(msg.get("Date"))
        has_attachment = _has_attachment(msg)
        body_preview = ""
        if fetch_body:
            body_preview = _extract_body_preview(msg, max_body_chars)

        return MailMeta(
            uid=uid,
            subject=subject,
            sender=sender,
            date=date,
            size_bytes=size_bytes,
            body_preview=body_preview,
            has_attachment=has_attachment,
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Main engine
# ---------------------------------------------------------------------------


class MailEngine:
    """
    Orchestrates IMAP connectivity, concurrent header fetching,
    heuristic / LLM analysis, and optional deletion.
    """

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self._conn: Optional[imaplib.IMAP4 | imaplib.IMAP4_SSL] = None

    # ---- connection management -------------------------------------------

    def connect(self) -> None:
        self._conn = _connect(self.cfg.imap)
        self._conn.select("INBOX")

    def disconnect(self) -> None:
        if self._conn:
            try:
                self._conn.logout()
            except Exception:
                pass
            self._conn = None

    def __enter__(self) -> "MailEngine":
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.disconnect()

    # ---- UID listing --------------------------------------------------------

    def list_uids(self) -> list[str]:
        """Return all UID strings in the INBOX (newest first)."""
        assert self._conn, "Not connected"
        status, data = self._conn.uid("search", None, "ALL")
        if status != "OK" or not data or data[0] is None:
            return []
        raw_uids = data[0].decode().split()
        raw_uids.reverse()  # newest first
        limit = self.cfg.scan_limit
        if limit:
            raw_uids = raw_uids[:limit]
        return raw_uids

    # ---- concurrent header fetching -----------------------------------------

    def fetch_headers_concurrent(
        self,
        uids: list[str],
        progress_cb: Optional[Callable[[MailMeta], None]] = None,
    ) -> list[MailMeta]:
        """
        Fetch headers for all UIDs using a thread pool.
        Each thread opens its own IMAP connection to avoid locking issues.
        Rate-limited to prevent exceeding Proton Mail API limits.
        """
        results: list[Optional[MailMeta]] = [None] * len(uids)
        need_body = self.cfg.mode == Mode.PRO

        limiter = RateLimiter(max_per_minute=200)

        def _worker(idx: int, uid: str) -> tuple[int, Optional[MailMeta]]:
            limiter.acquire()
            conn = _connect(self.cfg.imap)
            try:
                conn.select("INBOX")
                meta = _fetch_mail_meta(
                    conn,
                    uid,
                    fetch_body=need_body,
                    max_body_chars=self.cfg.ollama.max_body_chars,
                )
                return idx, meta
            finally:
                try:
                    conn.logout()
                except Exception:
                    pass

        with ThreadPoolExecutor(max_workers=self.cfg.max_workers) as executor:
            futures = {
                executor.submit(_worker, idx, uid): idx
                for idx, uid in enumerate(uids)
            }
            for future in as_completed(futures):
                try:
                    idx, meta = future.result()
                    if meta:
                        results[idx] = meta
                        if progress_cb:
                            progress_cb(meta)
                except Exception:
                    pass

        return [m for m in results if m is not None]

    def fetch_body_for_cached_mails(
        self,
        mails: list[MailMeta],
        progress_cb: Optional[Callable[[MailMeta], None]] = None,
    ) -> list[MailMeta]:
        """Fetch body preview for cached mails (for Pro mode analysis)."""
        results: list[Optional[MailMeta]] = [None] * len(mails)
        limiter = RateLimiter(max_per_minute=200)

        def _worker(idx: int, mail: MailMeta) -> tuple[int, Optional[MailMeta]]:
            limiter.acquire()
            conn = _connect(self.cfg.imap)
            try:
                conn.select("INBOX")
                body_preview = ""
                try:
                    fetch_parts = "(RFC822.SIZE RFC822)"
                    status, data = conn.uid("fetch", mail.uid, fetch_parts)
                    if status == "OK" and data and data[0]:
                        raw = data[0]
                        if isinstance(raw, tuple):
                            raw_bytes = raw[1]
                            msg = email.message_from_bytes(raw_bytes)
                            body_preview = _extract_body_preview(
                                msg, self.cfg.ollama.max_body_chars
                            )
                except Exception:
                    pass
                mail.body_preview = body_preview
                return idx, mail
            finally:
                try:
                    conn.logout()
                except Exception:
                    pass

        with ThreadPoolExecutor(max_workers=self.cfg.max_workers) as executor:
            futures = {
                executor.submit(_worker, idx, mail): idx
                for idx, mail in enumerate(mails)
            }
            for future in as_completed(futures):
                try:
                    idx, mail = future.result()
                    results[idx] = mail
                    if progress_cb:
                        progress_cb(mail)
                except Exception:
                    pass

        return [m for m in results if m is not None]

    # ---- analysis -----------------------------------------------------------

    def _analyze_single(
        self,
        meta: MailMeta,
        need_llm: bool,
    ) -> ScanResult:
        """Analyze a single mail. Used by parallel analyze."""
        from fast_analyzer import fast_analyze
        from pro_analyzer import pro_analyze

        if need_llm:
            fast_result = fast_analyze(meta)
            if fast_result.decision == "SIL":
                return pro_analyze(meta, self.cfg.ollama)
            return fast_result
        return fast_analyze(meta)

    def analyze(
        self,
        mails: list[MailMeta],
        progress_cb: Optional[Callable[[ScanResult], None]] = None,
    ) -> tuple[list[ScanResult], ScanStats]:
        """
        Analyze a list of :class:`MailMeta` objects.
        Returns (results, stats).
        
        Pro mode: Uses parallel LLM processing with ThreadPoolExecutor.
        """
        from fast_analyzer import fast_analyze
        from pro_analyzer import pro_analyze

        stats = ScanStats()
        scan_results: list[ScanResult] = [None] * len(mails)
        
        need_llm = self.cfg.mode == Mode.PRO
        
        if not need_llm:
            for idx, meta in enumerate(mails):
                stats.total_scanned += 1
                stats.total_size_bytes += meta.size_bytes
                result = fast_analyze(meta)
                scan_results[idx] = result
                if result.decision == "SIL":
                    stats.marked_for_deletion += 1
                    stats.marked_size_bytes += meta.size_bytes
                if progress_cb:
                    progress_cb(result)
        else:
            def _worker(args: tuple[int, MailMeta]) -> tuple[int, ScanResult]:
                idx, meta = args
                fast_result = fast_analyze(meta)
                if fast_result.decision == "SIL":
                    result = pro_analyze(meta, self.cfg.ollama)
                else:
                    result = fast_result
                return idx, result
            
            with ThreadPoolExecutor(max_workers=self.cfg.max_workers) as executor:
                futures = {
                    executor.submit(_worker, (idx, meta)): idx
                    for idx, meta in enumerate(mails)
                }
                
                for future in as_completed(futures):
                    try:
                        idx, result = future.result()
                        scan_results[idx] = result
                        
                        meta = mails[idx]
                        stats.total_scanned += 1
                        stats.total_size_bytes += meta.size_bytes
                        
                        if result.decision == "SIL":
                            stats.marked_for_deletion += 1
                            stats.marked_size_bytes += meta.size_bytes
                        
                        if progress_cb:
                            progress_cb(result)
                    except Exception:
                        pass

        scan_results = [r for r in scan_results if r is not None]
        return scan_results, stats

    # ---- deletion -----------------------------------------------------------

    def delete_mails(
        self,
        uids: list[str],
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> list[str]:
        """
        Mark messages as deleted and expunge.
        Returns list of UIDs successfully deleted.
        Rate-limited to prevent exceeding Proton Mail API limits (~60/min).
        """
        assert self._conn, "Not connected"
        deleted: list[str] = []
        limiter = RateLimiter(max_per_minute=60)
        for uid in uids:
            limiter.acquire()
            try:
                self._conn.uid("store", uid, "+FLAGS", r"(\Deleted)")
                deleted.append(uid)
                if progress_cb:
                    progress_cb(uid)
            except Exception as exc:
                pass
        self._conn.expunge()
        return deleted

    # ---- convenience scan + optional delete ---------------------------------

    def run(
        self,
        fetch_progress_cb: Optional[Callable[[MailMeta], None]] = None,
        analyze_progress_cb: Optional[Callable[[ScanResult], None]] = None,
        delete_progress_cb: Optional[Callable[[str], None]] = None,
    ) -> tuple[list[ScanResult], ScanStats]:
        """
        Full pipeline: fetch → analyze → (optionally) delete.
        """
        uids = self.list_uids()
        mails = self.fetch_headers_concurrent(uids, progress_cb=fetch_progress_cb)
        results, stats = self.analyze(mails, progress_cb=analyze_progress_cb)

        if not self.cfg.dry_run:
            delete_uids = [r.mail.uid for r in results if r.decision == "SIL"]
            if delete_uids:
                self.delete_mails(delete_uids, progress_cb=delete_progress_cb)

        return results, stats
