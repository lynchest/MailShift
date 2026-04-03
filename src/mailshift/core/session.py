"""
session.py | Testable progress handlers and workers for CLI orchestration.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional, Tuple

from ..config.config import AppConfig
from ..models.models import MailMeta, ScanResult
from .analyzers.fast import extract_fast_category
from .analyzers.pro import pro_analyze


class FetchProgressHandler:
    """Collects fetched mails and updates progress in throttled batches."""

    def __init__(
        self,
        mails: list[MailMeta],
        progress: object,
        task_id: int,
        total_count: int,
        clean_text_fn: Callable[[Optional[str], int], str],
        format_duration_fn: Callable[[float], str],
        batch_size: int = 10,
        now_fn: Callable[[], float] = time.perf_counter,
        start_time: Optional[float] = None,
    ) -> None:
        self._mails = mails
        self._progress = progress
        self._task_id = task_id
        self._total_count = total_count
        self._clean_text = clean_text_fn
        self._format_duration = format_duration_fn
        self._batch_size = max(1, batch_size)
        self._now_fn = now_fn
        self._start_time = now_fn() if start_time is None else start_time

        self._done = 0
        self._pending = 0

    def __call__(self, meta: MailMeta) -> None:
        self._mails.append(meta)
        self._done += 1
        self._pending += 1

        sender = self._clean_text(meta.sender, 20)
        if self._done >= 3:
            elapsed = max(0.001, self._now_fn() - self._start_time)
            remaining = max(0.0, (self._total_count - self._done) * (elapsed / self._done))
            current_label = f"{sender} | kalan {self._format_duration(remaining)}"
        else:
            current_label = f"{sender} | kalan hesaplaniyor"

        if self._pending >= self._batch_size or self._done == self._total_count:
            self._progress.update(self._task_id, advance=self._pending, current=current_label)
            self._pending = 0


class AnalyzeProgressHandler:
    """Collects analysis results and updates progress in throttled batches."""

    def __init__(
        self,
        scan_results: list[ScanResult],
        progress: object,
        task_id: int,
        total_count: int,
        clean_text_fn: Callable[[Optional[str], int], str],
        batch_size: int = 20,
    ) -> None:
        self._scan_results = scan_results
        self._progress = progress
        self._task_id = task_id
        self._total_count = total_count
        self._clean_text = clean_text_fn
        self._batch_size = max(1, batch_size)

        self._done = 0
        self._pending = 0

    def __call__(self, result: ScanResult) -> None:
        self._scan_results.append(result)
        self._done += 1
        self._pending += 1

        if self._pending >= self._batch_size or self._done == self._total_count:
            subject = self._clean_text(result.mail.subject, 24)
            decision = "SIL" if result.decision == "SIL" else "TUT"
            self._progress.update(self._task_id, advance=self._pending, current=f"{decision} {subject}")
            self._pending = 0


class LLMWorker:
    """Thread-pool worker wrapper for Phase-2 LLM verification."""

    def __init__(self, cfg: AppConfig, cancel_event: threading.Event) -> None:
        self._cfg = cfg
        self._cancel_event = cancel_event

    def __call__(self, idx_result: Tuple[int, ScanResult]) -> Tuple[int, ScanResult]:
        idx, candidate = idx_result

        if self._cancel_event.is_set():
            return idx, ScanResult(mail=candidate.mail, decision="TUT", reason="cancelled")

        llm_cfg = self._cfg.lm_studio if self._cfg.llm_backend == "lm_studio" else self._cfg.ollama
        result = pro_analyze(
            candidate.mail,
            llm_cfg,
            self._cfg.llm_backend,
            fast_reason=candidate.reason,
            fast_category=extract_fast_category(candidate.reason),
            cancel_event=self._cancel_event,
        )
        return idx, result
