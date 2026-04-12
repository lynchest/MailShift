"""
session.py | Testable progress handlers and workers for CLI orchestration.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import threading
import time
from typing import Callable, Optional, Tuple

from ..config.config import AppConfig
from ..models.models import MailMeta, ScanResult
from .analyzers.fast import extract_fast_category
from .analyzers.pro import pro_analyze, is_llm_error_reason, is_llm_timeout_reason


@dataclass(frozen=True)
class AdaptiveWindowSnapshot:
    sample_count: int
    timeout_rate: float
    error_rate: float
    p95_latency_s: float


@dataclass
class FetchProgressParams:
    """Parameters for FetchProgressHandler."""
    progress: object
    task_id: int
    total_count: int
    clean_text_fn: Callable[[Optional[str], int], str]
    format_duration_fn: Callable[[float], str]
    batch_size: int = 10
    now_fn: Callable[[], float] = time.perf_counter
    start_time: Optional[float] = None


@dataclass
class AnalyzeProgressParams:
    """Parameters for AnalyzeProgressHandler."""
    progress: object
    task_id: int
    total_count: int
    clean_text_fn: Callable[[Optional[str], int], str]
    batch_size: int = 20


class AdaptiveWorkerController:
    """Adaptive worker tuner using timeout/error rates and p95 latency signals."""

    def __init__(
        self,
        initial_workers: int,
        max_workers: int,
        min_workers: int = 1,
        backend: str = "ollama",
        timeout_seconds: float = 60.0,
        stable_windows_required: int = 2,
    ) -> None:
        safe_min = max(1, int(min_workers))
        safe_max = max(safe_min, int(max_workers))
        self.current_workers = max(safe_min, min(int(initial_workers), safe_max))
        self.max_workers = safe_max
        self.min_workers = safe_min
        self.backend = "lm_studio" if str(backend).lower() == "lm_studio" else "ollama"
        self.stable_windows_required = max(1, int(stable_windows_required))

        p95_overload = max(8.0, min(float(timeout_seconds) * 0.60, 30.0))
        if self.backend == "lm_studio":
            p95_overload *= 1.15
        self._p95_overload_s = p95_overload
        self._p95_stable_s = max(3.0, p95_overload * 0.55)

        self._stable_streak = 0
        self.adjustment_events: list[str] = []

        self._window_latencies: list[float] = []
        self._window_timeout_count = 0
        self._window_error_count = 0

        self._all_latencies: list[float] = []
        self._all_timeout_count = 0
        self._all_error_count = 0
        self._all_samples = 0

    @staticmethod
    def _compute_p95(values: list[float]) -> float:
        if not values:
            return 0.0
        ordered = sorted(max(0.0, float(v)) for v in values)
        index = max(0, min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1))
        return ordered[index]

    def _build_snapshot(self, latencies: list[float], timeout_count: int, error_count: int) -> AdaptiveWindowSnapshot:
        sample_count = len(latencies)
        if sample_count == 0:
            return AdaptiveWindowSnapshot(sample_count=0, timeout_rate=0.0, error_rate=0.0, p95_latency_s=0.0)
        return AdaptiveWindowSnapshot(
            sample_count=sample_count,
            timeout_rate=timeout_count / sample_count,
            error_rate=error_count / sample_count,
            p95_latency_s=self._compute_p95(latencies),
        )

    def observe(self, latency_s: float, reason: str) -> None:
        latency = max(0.0, float(latency_s))
        self._window_latencies.append(latency)
        self._all_latencies.append(latency)
        self._all_samples += 1

        if is_llm_timeout_reason(reason):
            self._window_timeout_count += 1
            self._all_timeout_count += 1
        elif is_llm_error_reason(reason):
            self._window_error_count += 1
            self._all_error_count += 1

    def evaluate_window(self) -> tuple[int, str, AdaptiveWindowSnapshot]:
        snapshot = self._build_snapshot(
            self._window_latencies,
            self._window_timeout_count,
            self._window_error_count,
        )

        if snapshot.sample_count == 0:
            return self.current_workers, "insufficient-data", snapshot

        overloaded = (
            snapshot.timeout_rate >= 0.10
            or snapshot.error_rate >= 0.15
            or snapshot.p95_latency_s >= self._p95_overload_s
        )
        stable = (
            snapshot.timeout_rate == 0.0
            and snapshot.error_rate <= 0.02
            and snapshot.p95_latency_s <= self._p95_stable_s
        )

        previous_workers = self.current_workers
        reason = "hold"

        if overloaded:
            decrease = max(1, previous_workers // 4)
            self.current_workers = max(self.min_workers, previous_workers - decrease)
            self._stable_streak = 0
            reason = (
                f"overload timeout-rate={snapshot.timeout_rate:.1%} "
                f"error-rate={snapshot.error_rate:.1%} p95={snapshot.p95_latency_s:.1f}s"
            )
        elif stable:
            self._stable_streak += 1
            if self._stable_streak >= self.stable_windows_required and previous_workers < self.max_workers:
                self.current_workers = min(self.max_workers, previous_workers + 1)
                self._stable_streak = 0
                reason = (
                    f"stable timeout-rate={snapshot.timeout_rate:.1%} "
                    f"error-rate={snapshot.error_rate:.1%} p95={snapshot.p95_latency_s:.1f}s"
                )
            else:
                reason = (
                    f"stable-hold timeout-rate={snapshot.timeout_rate:.1%} "
                    f"error-rate={snapshot.error_rate:.1%} p95={snapshot.p95_latency_s:.1f}s"
                )
        else:
            self._stable_streak = 0
            reason = (
                f"mixed timeout-rate={snapshot.timeout_rate:.1%} "
                f"error-rate={snapshot.error_rate:.1%} p95={snapshot.p95_latency_s:.1f}s"
            )

        if self.current_workers != previous_workers:
            self.adjustment_events.append(f"{previous_workers}->{self.current_workers} ({reason})")

        self._window_latencies.clear()
        self._window_timeout_count = 0
        self._window_error_count = 0
        return self.current_workers, reason, snapshot

    def overall_snapshot(self) -> AdaptiveWindowSnapshot:
        return self._build_snapshot(
            self._all_latencies,
            self._all_timeout_count,
            self._all_error_count,
        )


class FetchProgressHandler:
    """Collects fetched mails and updates progress in throttled batches."""

    def __init__(
        self,
        mails: list[MailMeta],
        params: FetchProgressParams,
    ) -> None:
        self._mails = mails
        self._progress = params.progress
        self._task_id = params.task_id
        self._total_count = params.total_count
        self._clean_text = params.clean_text_fn
        self._format_duration = params.format_duration_fn
        self._batch_size = max(1, params.batch_size)
        self._now_fn = params.now_fn
        self._start_time = (
            params.now_fn() if params.start_time is None else params.start_time
        )

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
        params: AnalyzeProgressParams,
    ) -> None:
        self._scan_results = scan_results
        self._progress = params.progress
        self._task_id = params.task_id
        self._total_count = params.total_count
        self._clean_text = params.clean_text_fn
        self._batch_size = max(1, params.batch_size)

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
