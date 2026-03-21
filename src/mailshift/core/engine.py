from __future__ import annotations

import email
import email.header
import imaplib
import re
import socket
import ssl
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from email.message import Message
from threading import Lock
from typing import Callable, Optional, TypeAlias

from bs4 import BeautifulSoup

from ..models.models import MailMeta, ScanResult, ScanStats
from ..config.config import AppConfig, IMAPConfig, Mode, Provider, RateLimitConfig
from ..utils.logger import log

# ---------------------------------------------------------------------------
# Type Aliases (Clean Code Optimizasyonu)
# ---------------------------------------------------------------------------
IMAPConnection: TypeAlias = imaplib.IMAP4 | imaplib.IMAP4_SSL

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# 25 KB altındaki maillerin gövdesi, header ile aynı anda çekilir.
# (Double fetch maliyetini düşürmek için eklendi)
SMALL_MAIL_THRESHOLD_BYTES = 25 * 1024 


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def chunk_list(lst: list, n: int):
    """Yield successive *n*-sized chunks from *lst*."""
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def _connect(cfg: IMAPConfig, timeout: int = 30) -> IMAPConnection:
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

    # HTML Parsing Optimizasyonu: CPU darboğazını önlemek için lxml kullanıyoruz
    try:
        final_text = text_body or BeautifulSoup(
            html_body, "lxml"
        ).get_text(separator=" ", strip=True)
    except Exception:
        # lxml kurulu değilse veya patlarsa varsayılan html.parser'a düş
        final_text = text_body or BeautifulSoup(
            html_body, "html.parser"
        ).get_text(separator=" ", strip=True)
        
    return final_text[:max_chars]


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------

def _with_retry(
    fn: Callable,
    rl: RateLimitConfig,
    label: str = "",
    on_error: Optional[Callable[[Exception, int], None]] = None,
):
    """Call *fn()* up to *rl.max_retries* times with exponential back-off."""
    delay = rl.retry_backoff
    last_exc: Exception | None = None
    for attempt in range(1, rl.max_retries + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if on_error:
                try:
                    on_error(exc, attempt)
                except Exception as hook_exc:
                    log.warning(
                        f"Retry hook failed for '{label}' on attempt {attempt}: "
                        f"{hook_exc}"
                    )
            if attempt < rl.max_retries:
                log.warning(
                    f"Retry {attempt}/{rl.max_retries} for '{label}' "
                    f"after {delay:.1f}s | {exc}"
                )
                time.sleep(delay)
                delay *= 2  # exponential back-off
            else:
                log.error(
                    f"All {rl.max_retries} retries exhausted for '{label}': {exc}"
                )
    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Bulk IMAP fetch
# ---------------------------------------------------------------------------

def _fetch_mails_bulk(
    conn: IMAPConnection,
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
        self._conn: Optional[IMAPConnection] = None

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

    def _is_connection_error(self, exc: Exception) -> bool:
        if isinstance(exc, (imaplib.IMAP4.abort, ssl.SSLError, socket.timeout, OSError)):
            return True
        msg = str(exc).lower()
        return any(
            token in msg
            for token in (
                "eof",
                "socket",
                "connection",
                "broken pipe",
                "timed out",
                "connection reset",
                "imap",
            )
        )

    def _recover_connection(self, exc: Exception, label: str, attempt: int) -> None:
        if not self._is_connection_error(exc):
            return
        self._force_reconnect(label, attempt)

    def _force_reconnect(self, label: str, attempt: int) -> None:
        log.warning(f"Forcing IMAP reconnect during '{label}' (attempt {attempt})")
        old_conn = self._conn
        self._conn = None

        if old_conn:
            try:
                old_conn.logout()
            except Exception:
                pass

        self._conn = _connect(self.cfg.imap, timeout=self._rl.connect_timeout)
        self._conn.select("INBOX")
        log.info("IMAP reconnect successful; INBOX re-selected")

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
        assert self._conn, "Not connected"
        from ..db.database import mark_uids_fetched, get_fetched_uids, save_mails_cache

        rl = self._rl

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
        chunk_delay = max(0.0, rl.chunk_delay)

        log.info(
            f"Fetching headers for {len(pending)} messages "
            f"in {total_chunks} chunks of {rl.fetch_chunk_size}"
        )

        for chunk_idx, chunk in enumerate(chunks, start=1):
            chunk_started = time.perf_counter()
            had_retry = False

            def _fetch(c=chunk):
                return _fetch_mails_bulk(
                    self._conn,
                    c,
                    fetch_body=False, 
                    max_body_chars=self.cfg.ollama.max_body_chars,
                )

            def _on_fetch_error(exc: Exception, attempt: int, lbl: str) -> None:
                nonlocal had_retry
                had_retry = True
                self._recover_connection(exc, lbl, attempt)

            try:
                chunk_results = _with_retry(
                    _fetch, rl, label=f"fetch chunk {chunk_idx}/{total_chunks}",
                    on_error=lambda exc, attempt, lbl=(
                        f"fetch chunk {chunk_idx}/{total_chunks}"
                    ): _on_fetch_error(exc, attempt, lbl),
                )
            except Exception as exc:
                log.error(f"Chunk {chunk_idx}/{total_chunks} failed permanently: {exc}")
                chunk_results = []

            # Dinamik Fetch Optimizasyonu: Küçük boyutlu maillerin body'sini hemen çek
            small_uids = [
                m.uid for m in chunk_results 
                if 0 < m.size_bytes <= SMALL_MAIL_THRESHOLD_BYTES
            ]
            if small_uids:
                try:
                    small_bodies = _fetch_mails_bulk(
                        self._conn, small_uids, fetch_body=True,
                        max_body_chars=self.cfg.ollama.max_body_chars
                    )
                    body_map = {m.uid: m.body_preview for m in small_bodies}
                    for meta in chunk_results:
                        if meta.uid in body_map:
                            meta.body_preview = body_map[meta.uid]
                except Exception as e:
                    log.warning(f"Failed dynamic body fetch for chunk {chunk_idx}: {e}")

            chunk_elapsed = time.perf_counter() - chunk_started

            for meta in chunk_results:
                results.append(meta)
                if progress_cb:
                    progress_cb(meta)

            if chunk_results:
                mark_uids_fetched([m.uid for m in chunk_results])
                save_mails_cache(chunk_results, batch_size=rl.db_batch_size)

            if had_retry:
                base = max(chunk_delay, rl.chunk_delay)
                chunk_delay = min(0.5, (base * 1.5) + 0.01)
            elif chunk_elapsed < 0.4 and chunk_delay > 0:
                chunk_delay = max(0.0, (chunk_delay * 0.85) - 0.005)

            if chunk_idx < total_chunks and chunk_delay > 0:
                time.sleep(chunk_delay)

        log.info(f"Successfully fetched {len(results)} message headers")
        return results

    def fetch_body_for_cached_mails(
        self,
        mails: list[MailMeta],
        progress_cb: Optional[Callable[[MailMeta], None]] = None,
    ) -> list[MailMeta]:
        assert self._conn, "Not connected"
        from ..db.database import save_mails_cache

        rl = self._rl
        # Zaten body_preview'ı dolu olanları elediğimizden emin oluyoruz
        uids = [m.uid for m in mails if not m.body_preview]
        if not uids:
            return mails
            
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
                    self._conn,
                    c,
                    fetch_body=True,
                    max_body_chars=self.cfg.ollama.max_body_chars,
                )

            try:
                chunk_results = _with_retry(
                    _fetch, rl, label=f"body chunk {chunk_idx}/{total_chunks}",
                    on_error=lambda exc, attempt, lbl=(
                        f"body chunk {chunk_idx}/{total_chunks}"
                    ): self._recover_connection(exc, lbl, attempt),
                )
            except Exception as exc:
                log.error(f"Body chunk {chunk_idx}/{total_chunks} failed permanently: {exc}")
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

        save_mails_cache(mails, batch_size=rl.db_batch_size)
        return mails

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def analyze(
        self,
        mails: list[MailMeta],
        progress_cb: Optional[Callable[[ScanResult], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> tuple[list[ScanResult], ScanStats]:
        from .analyzers.fast import fast_analyze
        from .analyzers.pro import pro_analyze
        from ..utils.hardware import calculate_optimal_workers

        stats = ScanStats()
        stats_lock = Lock()
        need_llm = self.cfg.mode == Mode.PRO

        # ── Phase 1: Fast heuristic scan ──
        fast_results: list[ScanResult] = []
        for meta in mails:
            if cancel_event and cancel_event.is_set():
                break
            res = fast_analyze(meta)
            fast_results.append(res)
        
        if not need_llm or (cancel_event and cancel_event.is_set()):
            for res in fast_results:
                self._record_stats(stats, stats_lock, res, progress_cb)
            return fast_results, stats

        # ── Phase 2: Batch fetch bodies only for SIL candidates ──
        sil_candidates = [r for r in fast_results if r.decision == "SIL"]
        tut_results = [r for r in fast_results if r.decision == "TUT"]
        
        need_body_mails = [r.mail for r in sil_candidates if not r.mail.body_preview]
        if need_body_mails:
            log.info(f"analyze(): Fetching bodies for {len(need_body_mails)} candidates")
            self.fetch_body_for_cached_mails(need_body_mails)

        # ── Phase 3: Parallel LLM verification ──
        max_workers = calculate_optimal_workers(
            self.cfg.ollama.model,
            self.cfg.mode.value,
            manual_workers=self.cfg.max_workers,
            backend=self.cfg.llm_backend
        )
        
        llm_results: list[ScanResult | None] = [None] * len(sil_candidates)

        def _llm_worker(idx: int, candidate: ScanResult) -> tuple[int, ScanResult]:
            if cancel_event and cancel_event.is_set():
                return idx, ScanResult(mail=candidate.mail, decision="TUT", reason="cancelled")
            
            llm_cfg = self.cfg.lm_studio if self.cfg.llm_backend == "lm_studio" else self.cfg.ollama
            # Timeout mekanizması pro_analyze içinde HTTP client seviyesinde ele alınmalıdır.
            res = pro_analyze(
                candidate.mail, 
                llm_cfg, 
                backend=self.cfg.llm_backend, 
                fast_reason=candidate.reason,
                cancel_event=cancel_event
            )
            return idx, res

        # Model takılmalarını engellemek için agresif timeout takibi
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_idx = {
                executor.submit(_llm_worker, i, c): i 
                for i, c in enumerate(sil_candidates)
            }
            # Eğer local model (VRAM dolması vs.) yanıt vermezse worker kilitlenmesini önle
            timeout_limit = 60.0 
            
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    # Timeout limitini burada da uyguluyoruz
                    _, res = future.result(timeout=timeout_limit)
                    llm_results[idx] = res
                except TimeoutError:
                    log.error(f"LLM worker timeout ({timeout_limit}s) for candidate {idx}")
                    llm_results[idx] = ScanResult(
                        mail=sil_candidates[idx].mail, 
                        decision="TUT", 
                        reason="analyze-timeout"
                    )
                except Exception as exc:
                    log.warning(f"LLM worker failed for candidate {idx}: {exc}")
                    llm_results[idx] = ScanResult(
                        mail=sil_candidates[idx].mail, 
                        decision="TUT", 
                        reason=f"analyze-error:{exc}"
                    )

        final_results = tut_results + [r for r in llm_results if r is not None]
        for res in final_results:
            self._record_stats(stats, stats_lock, res, progress_cb)

        return final_results, stats

    def _record_stats(self, stats: ScanStats, lock: Lock, res: ScanResult, cb: Optional[Callable]) -> None:
        with lock:
            stats.total_scanned += 1
            stats.total_size_bytes += res.mail.size_bytes
            if res.decision == "SIL":
                stats.marked_for_deletion += 1
                stats.marked_size_bytes += res.mail.size_bytes
        if cb:
            cb(res)

    # ------------------------------------------------------------------
    # Deletion / trash
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
                status, _ = self._conn.uid("store", u, "+FLAGS", r"(\Deleted)")
                if status != "OK":
                    raise RuntimeError(f"STORE returned {status}")

            try:
                _with_retry(
                    _store,
                    rl,
                    label=f"delete chunk {chunk_idx}/{total_chunks}",
                    on_error=lambda exc, attempt, lbl=(
                        f"delete chunk {chunk_idx}/{total_chunks}"
                    ): self._recover_connection(exc, lbl, attempt),
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
        try:
            _with_retry(
                lambda: self._conn.expunge(),
                rl,
                label="delete expunge",
                on_error=lambda exc, attempt: self._recover_connection(
                    exc, "delete expunge", attempt
                ),
            )
        except Exception as exc:
            log.error(f"Delete expunge failed after retries: {exc}")
            return []

        log.info(f"Deleted {len(deleted)} messages successfully")
        return deleted

    def _resolve_trash_folders(self, hint: str) -> list[str]:
        candidates: list[str] = []
        if hint:
            candidates.append(hint.strip('"'))

        try:
            status, lines = self._conn.list('""', "*")
            if status == "OK":
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
                    keywords = ("trash", "bin", "deleted", "çöp", "silinmiş", "papelera", "corbeille", "papierkorb")
                    has_keyword = any(k in name.lower() for k in keywords)
                    if has_trash_flag or has_keyword:
                        candidates.append(name)
            else:
                log.warning(f"Trash folder LIST returned status={status}")
        except Exception as e:
            log.warning(f"Trash folder discovery failed: {e}")

        if self.cfg.provider == Provider.GMAIL:
            candidates.extend([
                "[Gmail]/Trash", "[Google Mail]/Trash", "[Gmail]/Bin",
                "[Google Mail]/Bin", "Trash",
            ])

        deduped: list[str] = []
        seen: set[str] = set()
        for folder in candidates:
            clean_folder = folder.strip().strip('"')
            if not clean_folder:
                continue
            key = clean_folder.casefold()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(clean_folder)

        if not deduped:
            deduped = ["Trash"]

        if len(deduped) == 1 and deduped[0] == hint.strip('"'):
            log.warning(f"No trash folder found via LIST; falling back to: {deduped[0]}")
        else:
            log.debug(f"Trash folder candidates: {deduped}")

        return deduped

    def move_to_trash(
        self,
        uids: list[str],
        trash_folder: str = "Trash",
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> list[str]:
        assert self._conn, "Not connected"
        rl = self._rl
        folders = self._resolve_trash_folders(trash_folder)
        moved: list[str] = []
        chunks = list(chunk_list(uids, rl.delete_chunk_size))
        total_chunks = len(chunks)
        log.info(
            f"Moving {len(uids)} messages to trash folders {folders} "
            f"in {total_chunks} chunks"
        )

        for chunk_idx, chunk in enumerate(chunks, start=1):
            uid_str = ",".join(chunk)

            # --- HATA DÜZELTİLDİ: Copy ve Store işlemleri ayrıldı ---
            def _copy(u=uid_str):
                last_status = "NO"
                for folder in folders:
                    status, _ = self._conn.uid("copy", u, f'"{folder}"')
                    if status == "OK":
                        return  # Başarılı kopyalama, Store işi diğer adıma bırakıldı.
                    last_status = status
                    log.debug(f"COPY returned {status} for trash folder '{folder}', trying next candidate")
                raise RuntimeError(f"COPY returned {last_status}")

            def _store_deleted(u=uid_str):
                store_status, _ = self._conn.uid("store", u, "+FLAGS", r"(\Deleted)")
                if store_status != "OK":
                    raise RuntimeError(f"STORE returned {store_status}")

            try:
                # 1. Aşama: Güvenli Kopyalama
                _with_retry(
                    _copy, rl, label=f"trash chunk copy {chunk_idx}/{total_chunks}",
                    on_error=lambda exc, attempt, lbl=(
                        f"trash chunk copy {chunk_idx}/{total_chunks}"
                    ): self._force_reconnect(lbl, attempt),
                )
                
                # 2. Aşama: Etiketleme (Eğer koparsa kopyalama baştan yapılmaz, sadece bu denenir)
                _with_retry(
                    _store_deleted, rl, label=f"trash chunk store {chunk_idx}/{total_chunks}",
                    on_error=lambda exc, attempt, lbl=(
                        f"trash chunk store {chunk_idx}/{total_chunks}"
                    ): self._force_reconnect(lbl, attempt),
                )
                
                moved.extend(chunk)
                if progress_cb:
                    for uid in chunk:
                        progress_cb(uid)
            except Exception as e:
                log.error(f"Trash chunk {chunk_idx}/{total_chunks} failed: {e}")

            if chunk_idx < total_chunks and rl.chunk_delay > 0:
                time.sleep(rl.chunk_delay)

        try:
            _with_retry(
                lambda: self._conn.expunge(), rl, label="trash expunge",
                on_error=lambda exc, attempt: self._recover_connection(exc, "trash expunge", attempt),
            )
        except Exception as exc:
            log.error(f"Trash expunge failed after retries: {exc}")
            return []

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
        log.info("Starting Engine run")
        try:
            self.connect()
            uids = self.list_uids()
            if not uids:
                log.info("No messages found.")
                return [], ScanStats()

            mails = self.fetch_headers_concurrent(
                uids, progress_cb=fetch_progress_cb, resume=resume
            )
            results, stats = self.analyze(mails, progress_cb=analyze_progress_cb)

            if not self.cfg.dry_run and (
                delete_uids := [r.mail.uid for r in results if r.decision == "SIL"]
            ):
                self.delete_mails(delete_uids, progress_cb=delete_progress_cb)

            return results, stats
        finally:
            self.disconnect()