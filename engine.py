"""
engine.py – Mail fetching, heuristic analysis, Ollama LLM analysis, and deletion.
"""

from __future__ import annotations

import email
import email.header
import imaplib
import ssl
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from email.message import Message
from typing import TYPE_CHECKING, Callable, Iterator, Optional

import requests

from models import MailMeta, ScanResult, ScanStats
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


def _extract_body_preview(msg: Message, max_chars: int = 500) -> str:
    """Extract the first *max_chars* characters of the plain-text body."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if ct == "text/plain" and "attachment" not in cd:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    text = payload.decode(charset, errors="replace")
                    return text[:max_chars]
    else:
        if msg.get_content_type() == "text/plain":
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="replace")
                return text[:max_chars]
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
        """
        results: list[Optional[MailMeta]] = [None] * len(uids)
        need_body = self.cfg.mode == Mode.PRO

        def _worker(idx: int, uid: str) -> tuple[int, Optional[MailMeta]]:
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

    # ---- analysis -----------------------------------------------------------

    def analyze(
        self,
        mails: list[MailMeta],
        progress_cb: Optional[Callable[[ScanResult], None]] = None,
    ) -> tuple[list[ScanResult], ScanStats]:
        """
        Analyze a list of :class:`MailMeta` objects.
        Returns (results, stats).
        """
        from fast_analyzer import fast_analyze
        from pro_analyzer import pro_analyze

        stats = ScanStats()
        scan_results: list[ScanResult] = []

        for meta in mails:
            stats.total_scanned += 1
            stats.total_size_bytes += meta.size_bytes

            if self.cfg.mode == Mode.PRO:
                # Two-tier: fast pass first, then LLM for flagged mails
                fast_result = fast_analyze(meta)
                if fast_result.decision == "SIL":
                    result = pro_analyze(meta, self.cfg.ollama)
                else:
                    result = fast_result
            else:
                result = fast_analyze(meta)

            if result.decision == "SIL":
                stats.marked_for_deletion += 1
                stats.marked_size_bytes += meta.size_bytes

            scan_results.append(result)
            if progress_cb:
                progress_cb(result)

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
        """
        assert self._conn, "Not connected"
        deleted: list[str] = []
        for uid in uids:
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
