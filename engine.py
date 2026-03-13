from __future__ import annotations

import email
import email.header
import imaplib
import re
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.message import Message
from threading import Lock
from typing import Callable, Optional

from bs4 import BeautifulSoup

from models import MailMeta, ScanResult, ScanStats
from config import AppConfig, IMAPConfig, Mode, RateLimitConfig
from logger import log


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def chunk_list(lst: list, n: int):
    """Yield successive *n*-sized chunks from *lst*."""
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def _connect(cfg: IMAPConfig, timeout: int = 30) -> imaplib.IMAP4 | imaplib.IMAP4_SSL:
    """Open an IMAP connection with an explicit socket timeout."""
    socket.setdefaulttimeout(timeout)
    conn = (
        imaplib.IMAP4_SSL(cfg.host, cfg.port, ssl_context=ssl.create_default_context())
        if cfg.use_ssl
        else imaplib.IMAP4(cfg.host, cfg.port)
    )
    conn.login(cfg.username, cfg.password.get_secret_value())
    return conn


def _decode_header_value(raw: str | bytes | None) -> str:
    if not raw:
        return ""
    raw = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    return " ".join(
        p.decode(c or "utf-8", errors="replace") if isinstance(p, bytes) else str(p)
        for p, c in email.header.decode_header(raw)
    )


def _has_attachment(msg: Message) -> bool:
    parts = msg.walk() if msg.is_multipart() else [msg]
    return any(
        "attachment" in str(p.get("Content-Disposition", "")).lower() for p in parts
    )


def _extract_body_preview(msg: Message, max_chars: int = 500) -> str:
    text_body, html_body = "", ""
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if "attachment" in str(part.get("Content-Disposition", "")).lower():
            continue
        ct = part.get_content_type()
        if ct in ("text/plain", "text/html") and (
            payload := part.get_payload(decode=True)
        ):
            decoded = payload.decode(
                part.get_content_charset() or "utf-8", errors="replace"
            )
            if ct == "text/plain" and not text_body:
                text_body = decoded
            elif ct == "text/html" and not html_body:
                html_body = decoded

    final_text = text_body or BeautifulSoup(
        html_body, "html.parser"
    ).get_text(separator=" ", strip=True)
    return final_text[:max_chars]


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------

def _with_retry(fn: Callable, rl: RateLimitConfig, label: str = ""):
    """Call *fn()* up to *rl.max_retries* times with exponential back-off.

    Raises the last exception if all attempts fail.
    """
    delay = rl.retry_backoff
    last_exc: Exception | None = None
    for attempt in range(1, rl.max_retries + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt < rl.max_retries:
                log.warning(
                    f"Retry {attempt}/{rl.max_retries} for '{label}' "
                    f"after {delay:.1f}s – {exc}"
                )
                time.sleep(delay)
                delay *= 2  # exponential back-off
            else:
                log.error(
                    f"All {rl.max_retries} retries exhausted for '{label}': {exc}"
                )
    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Bulk IMAP fetch (single chunk, no retry — callers wrap with _with_retry)
# ---------------------------------------------------------------------------

def _fetch_mails_bulk(
    conn: imaplib.IMAP4 | imaplib.IMAP4_SSL,
    uids: list[str],
    fetch_body: bool = False,
    max_body_chars: int = 500,
) -> list[MailMeta]:
    """Fetch a single pre-sized chunk of UIDs; raises on IMAP error."""
    if not uids:
        return []
    uid_str = ",".join(uids)
    query = "(RFC822.SIZE RFC822)" if fetch_body else "(RFC822.SIZE RFC822.HEADER)"

    status, data = conn.uid("fetch", uid_str, query)
    if status != "OK" or not data:
        raise RuntimeError(f"FETCH returned status={status}")

    results: list[MailMeta] = []
    for item in data:
        if not isinstance(item, tuple):
            continue
        try:
            msg_info = item[0].decode(errors="replace")
            uid_match = re.search(r"UID\s+(\d+)", msg_info, re.IGNORECASE)
            parsed_uid = uid_match.group(1) if uid_match else None

            size_match = re.search(r"RFC822\.SIZE\s+(\d+)", msg_info, re.IGNORECASE)
            size_bytes = int(size_match.group(1)) if size_match else 0

            msg = email.message_from_bytes(item[1])

            if parsed_uid:
                results.append(
                    MailMeta(
                        uid=parsed_uid,
                        subject=_decode_header_value(msg.get("Subject")),
                        sender=_decode_header_value(msg.get("From")),
                        date=_decode_header_value(msg.get("Date")),
                        size_bytes=size_bytes,
                        body_preview=(
                            _extract_body_preview(msg, max_body_chars)
                            if fetch_body
                            else ""
                        ),
                        has_attachment=_has_attachment(msg),
                    )
                )
        except Exception:
            continue
    return results


# ---------------------------------------------------------------------------
# MailEngine
# ---------------------------------------------------------------------------

class MailEngine:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self._rl: RateLimitConfig = cfg.rate_limit
        self._conn: Optional[imaplib.IMAP4 | imaplib.IMAP4_SSL] = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> None:
        log.info(
            f"Connecting to IMAP server at "
            f"{self.cfg.imap.host}:{self.cfg.imap.port} "
            f"(timeout={self._rl.connect_timeout}s)"
        )
        self._conn = _connect(self.cfg.imap, timeout=self._rl.connect_timeout)
        self._conn.select("INBOX")
        log.info("IMAP connection established and INBOX selected")

    def disconnect(self) -> None:
        if self._conn:
            log.info("Disconnecting from IMAP server")
            try:
                self._conn.logout()
            except Exception as e:
                log.warning(f"Error disconnecting: {e}")
            self._conn = None

    def __enter__(self) -> MailEngine:
        self.connect()
        return self

    def __exit__(self, *_) -> None:
        self.disconnect()

    # ------------------------------------------------------------------
    # UID listing
    # ------------------------------------------------------------------

    def list_uids(self) -> list[str]:
        assert self._conn, "Not connected"
        log.info("Searching for ALL messages in INBOX")
        status, data = self._conn.uid("search", None, "ALL")
        if status != "OK" or not data or not data[0]:
            log.warning("No messages found or search failed")
            return []
        uids = data[0].decode().split()[::-1]
        log.info(f"Found {len(uids)} total messages in INBOX")
        return uids[: self.cfg.scan_limit] if self.cfg.scan_limit else uids

    # ------------------------------------------------------------------
    # Header / body fetching — rate-limit + retry + checkpoint
    # ------------------------------------------------------------------

    def fetch_headers_concurrent(
        self,
        uids: list[str],
        progress_cb: Optional[Callable[[MailMeta], None]] = None,
        resume: bool = False,
    ) -> list[MailMeta]:
        """Fetch headers (and optionally bodies) for *uids*.

        Parameters
        ----------
        uids:
            Full UID list returned by :meth:`list_uids`.
        progress_cb:
            Called once for every successfully fetched :class:`MailMeta`.
        resume:
            Skip UIDs already recorded in the checkpoint table so an
            interrupted run can continue from where it left off.
        """
        assert self._conn, "Not connected"
        from database import mark_uids_fetched, get_fetched_uids, save_mails_cache

        need_body = self.cfg.mode == Mode.PRO
        rl = self._rl

        # --- checkpoint: skip already-fetched UIDs ---
        if resume:
            done = get_fetched_uids()
            pending = [u for u in uids if u not in done]
            log.info(
                f"Resume mode: {len(done)} already fetched, "
                f"{len(pending)} remaining"
            )
        else:
            pending = uids

        results: list[MailMeta] = []
        chunks = list(chunk_list(pending, rl.fetch_chunk_size))
        total_chunks = len(chunks)

        log.info(
            f"Fetching headers for {len(pending)} messages "
            f"in {total_chunks} chunks of {rl.fetch_chunk_size}"
        )

        for chunk_idx, chunk in enumerate(chunks, start=1):
            def _fetch(c=chunk):
                return _fetch_mails_bulk(
                    self._conn,  # type: ignore[arg-type]
                    c,
                    fetch_body=need_body,
                    max_body_chars=self.cfg.ollama.max_body_chars,
                )

            try:
                chunk_results = _with_retry(
                    _fetch, rl, label=f"fetch chunk {chunk_idx}/{total_chunks}"
                )
            except Exception as exc:
                log.error(
                    f"Chunk {chunk_idx}/{total_chunks} failed permanently, "
                    f"skipping {len(chunk)} UIDs: {exc}"
                )
                chunk_results = []

            for meta in chunk_results:
                results.append(meta)
                if progress_cb:
                    progress_cb(meta)

            # Checkpoint: persist so we can resume if interrupted
            if chunk_results:
                mark_uids_fetched([m.uid for m in chunk_results])
                # Incremental batch-commit to SQLite cache
                save_mails_cache(chunk_results, batch_size=rl.db_batch_size)

            # Rate limiting: brief pause between consecutive IMAP requests
            if chunk_idx < total_chunks and rl.chunk_delay > 0:
                time.sleep(rl.chunk_delay)

        log.info(f"Successfully fetched {len(results)} message headers")
        return results

    def fetch_body_for_cached_mails(
        self,
        mails: list[MailMeta],
        progress_cb: Optional[Callable[[MailMeta], None]] = None,
    ) -> list[MailMeta]:
        assert self._conn, "Not connected"
        from database import save_mails_cache

        rl = self._rl
        uids = [m.uid for m in mails]
        fetched_dict: dict[str, str] = {}
        chunks = list(chunk_list(uids, rl.fetch_chunk_size))
        total_chunks = len(chunks)

        log.info(
            f"Fetching bodies for {len(uids)} cached messages "
            f"in {total_chunks} chunks"
        )

        for chunk_idx, chunk in enumerate(chunks, start=1):
            def _fetch(c=chunk):
                return _fetch_mails_bulk(
                    self._conn,  # type: ignore[arg-type]
                    c,
                    fetch_body=True,
                    max_body_chars=self.cfg.ollama.max_body_chars,
                )

            try:
                chunk_results = _with_retry(
                    _fetch, rl, label=f"body chunk {chunk_idx}/{total_chunks}"
                )
            except Exception as exc:
                log.error(
                    f"Body chunk {chunk_idx}/{total_chunks} failed permanently: {exc}"
                )
                chunk_results = []

            for meta in chunk_results:
                fetched_dict[meta.uid] = meta.body_preview

            if chunk_idx < total_chunks and rl.chunk_delay > 0:
                time.sleep(rl.chunk_delay)

        for mail in mails:
            if mail.uid in fetched_dict:
                mail.body_preview = fetched_dict[mail.uid]
            if progress_cb:
                progress_cb(mail)

        # Persist updated body previews in batches
        save_mails_cache(mails, batch_size=rl.db_batch_size)
        return mails

    # ------------------------------------------------------------------
    # Internal helper kept for backward-compatibility
    # ------------------------------------------------------------------

    def _execute_concurrent(
        self, worker_fn: Callable, items: list, max_workers: int
    ) -> list:
        results = [None] * len(items)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(worker_fn, idx, item): idx
                for idx, item in enumerate(items)
            }
            for f in as_completed(futures):
                try:
                    idx, res = f.result()
                    results[idx] = res
                except Exception:
                    pass
        return [r for r in results if r is not None]

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def analyze(
        self,
        mails: list[MailMeta],
        progress_cb: Optional[Callable[[ScanResult], None]] = None,
    ) -> tuple[list[ScanResult], ScanStats]:
        from fast_analyzer import fast_analyze
        from pro_analyzer import pro_analyze
        from hardware import calculate_optimal_workers

        stats = ScanStats()
        stats_lock = Lock()
        need_llm = self.cfg.mode == Mode.PRO

        max_workers = (
            calculate_optimal_workers(self.cfg.ollama.model, self.cfg.mode.value)
            if need_llm
            else max(1, __import__("os").cpu_count() or 4)
        )
        log.info(
            f"analyze(): {len(mails)} mails, {max_workers} workers, "
            f"LLM={'yes' if need_llm else 'no'}"
        )

        final_results: list[ScanResult | None] = [None] * len(mails)

        def _process(idx: int, meta: MailMeta) -> tuple[int, ScanResult]:
            """Run fast heuristic, then optionally LLM, for a single mail."""
            res = fast_analyze(meta)
            if need_llm and res.decision == "SIL":
                res = pro_analyze(meta, self.cfg.ollama)
            return idx, res

        def _record(idx: int, res: ScanResult) -> None:
            final_results[idx] = res
            with stats_lock:
                stats.total_scanned += 1
                stats.total_size_bytes += res.mail.size_bytes
                if res.decision == "SIL":
                    stats.marked_for_deletion += 1
                    stats.marked_size_bytes += res.mail.size_bytes
            if progress_cb:
                progress_cb(res)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(_process, idx, meta): idx
                for idx, meta in enumerate(mails)
            }
            for future in as_completed(future_map):
                try:
                    idx, res = future.result()
                    _record(idx, res)
                except Exception as exc:
                    idx = future_map[future]
                    log.warning(f"analyze task failed for mail index {idx}: {exc}")
                    # Fallback: keep mail to stay safe
                    _record(
                        idx,
                        ScanResult(
                            mail=mails[idx], decision="TUT", reason="analyze-error"
                        ),
                    )

        return [r for r in final_results if r is not None], stats

    # ------------------------------------------------------------------
    # Deletion / trash — rate-limit + retry on every chunk
    # ------------------------------------------------------------------

    def delete_mails(
        self,
        uids: list[str],
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> list[str]:
        assert self._conn, "Not connected"
        rl = self._rl
        deleted: list[str] = []
        chunks = list(chunk_list(uids, rl.delete_chunk_size))
        total_chunks = len(chunks)
        log.info(f"Deleting {len(uids)} messages in {total_chunks} chunks")

        for chunk_idx, chunk in enumerate(chunks, start=1):
            uid_str = ",".join(chunk)

            def _store(u=uid_str):
                status, _ = self._conn.uid(  # type: ignore[union-attr]
                    "store", u, "+FLAGS", r"(\Deleted)"
                )
                if status != "OK":
                    raise RuntimeError(f"STORE returned {status}")

            try:
                _with_retry(
                    _store, rl, label=f"delete chunk {chunk_idx}/{total_chunks}"
                )
                deleted.extend(chunk)
                if progress_cb:
                    for uid in chunk:
                        progress_cb(uid)
            except Exception as e:
                log.error(f"Delete chunk {chunk_idx}/{total_chunks} failed: {e}")

            if chunk_idx < total_chunks and rl.chunk_delay > 0:
                time.sleep(rl.chunk_delay)

        log.info("Expunging deleted messages")
        self._conn.expunge()
        log.info(f"Deleted {len(deleted)} messages successfully")
        return deleted

    def _resolve_trash_folder(self, hint: str) -> str:
        """Find the actual trash folder by listing all IMAP folders."""
        try:
            status, lines = self._conn.list('""', "*")  # type: ignore[union-attr]
            if status != "OK":
                return hint
            for line in lines:
                if not line:
                    continue
                text = line.decode() if isinstance(line, bytes) else line
                m = re.search(r'"([^"]+)"\s*$|(\S+)\s*$', text)
                if not m:
                    continue
                name = (m.group(1) or m.group(2)).strip('"')
                lower_text = text.lower()
                has_trash_flag = r"\trash" in lower_text
                keywords = (
                    "trash", "bin", "deleted",
                    "çöp", "silinmiş", "papelera", "corbeille", "papierkorb",
                )
                has_keyword = any(k in name.lower() for k in keywords)
                if has_trash_flag or has_keyword:
                    log.debug(f"Resolved trash folder via LIST: {name}")
                    return name
        except Exception as e:
            log.warning(f"Trash folder discovery failed: {e}")
        log.warning(f"No trash folder found via LIST; falling back to: {hint}")
        return hint

    def move_to_trash(
        self,
        uids: list[str],
        trash_folder: str = "Trash",
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> list[str]:
        assert self._conn, "Not connected"
        rl = self._rl
        folder = self._resolve_trash_folder(trash_folder)
        quoted_folder = f'"{folder}"'
        moved: list[str] = []
        chunks = list(chunk_list(uids, rl.delete_chunk_size))
        total_chunks = len(chunks)
        log.info(
            f"Moving {len(uids)} messages to trash folder: {folder} "
            f"in {total_chunks} chunks"
        )

        for chunk_idx, chunk in enumerate(chunks, start=1):
            uid_str = ",".join(chunk)

            def _copy(u=uid_str):
                status, _ = self._conn.uid(  # type: ignore[union-attr]
                    "copy", u, quoted_folder
                )
                if status != "OK":
                    raise RuntimeError(f"COPY returned {status}")
                self._conn.uid("store", u, "+FLAGS", r"(\Deleted)")  # type: ignore[union-attr]

            try:
                _with_retry(
                    _copy, rl, label=f"trash chunk {chunk_idx}/{total_chunks}"
                )
                moved.extend(chunk)
                if progress_cb:
                    for uid in chunk:
                        progress_cb(uid)
            except Exception as e:
                log.error(f"Trash chunk {chunk_idx}/{total_chunks} failed: {e}")

            if chunk_idx < total_chunks and rl.chunk_delay > 0:
                time.sleep(rl.chunk_delay)

        self._conn.expunge()
        log.info(f"Moved {len(moved)} messages to trash successfully")
        return moved

    # ------------------------------------------------------------------
    # High-level convenience entry-point
    # ------------------------------------------------------------------

    def run(
        self,
        fetch_progress_cb: Optional[Callable] = None,
        analyze_progress_cb: Optional[Callable] = None,
        delete_progress_cb: Optional[Callable] = None,
        resume: bool = False,
    ) -> tuple[list[ScanResult], ScanStats]:
        """Full scan-and-optionally-delete pipeline.

        Parameters
        ----------
        resume:
            Pass ``True`` to skip UIDs already checkpointed from a previous
            interrupted run.
        """
        uids = self.list_uids()
        mails = self.fetch_headers_concurrent(
            uids, progress_cb=fetch_progress_cb, resume=resume
        )
        results, stats = self.analyze(mails, progress_cb=analyze_progress_cb)

        if not self.cfg.dry_run and (
            delete_uids := [r.mail.uid for r in results if r.decision == "SIL"]
        ):
            self.delete_mails(delete_uids, progress_cb=delete_progress_cb)

        return results, stats
