import threading

from mailshift.config.config import AppConfig, Mode, Provider, build_imap_config
from mailshift.core import session as session_helpers
from mailshift.models.models import MailMeta, ScanResult


class ProgressSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, str]] = []

    def update(self, task_id: int, advance: int, current: str) -> None:
        self.calls.append((task_id, advance, current))


def _make_cfg(backend: str = "ollama") -> AppConfig:
    imap = build_imap_config(Provider.GMAIL, "u@g.com", "p")
    return AppConfig(provider=Provider.GMAIL, mode=Mode.PRO, imap=imap, llm_backend=backend)


def test_fetch_progress_handler_batches_updates() -> None:
    mails: list[MailMeta] = []
    progress = ProgressSpy()

    # start_time and now_fn are deterministic for stable assertions
    ticks = iter([11.0, 12.0, 13.0])
    handler = session_helpers.FetchProgressHandler(
        mails=mails,
        progress=progress,
        task_id=1,
        total_count=3,
        clean_text_fn=lambda text, _max_len: (text or "unknown"),
        format_duration_fn=lambda seconds: f"{int(seconds)} sn",
        batch_size=2,
        start_time=10.0,
        now_fn=lambda: next(ticks),
    )

    handler(MailMeta(uid="1", sender="alice"))
    handler(MailMeta(uid="2", sender="bob"))

    assert len(mails) == 2
    assert len(progress.calls) == 1
    assert progress.calls[0] == (1, 2, "bob | kalan hesaplaniyor")

    handler(MailMeta(uid="3", sender="charlie"))

    assert len(mails) == 3
    assert len(progress.calls) == 2
    assert progress.calls[1][0] == 1
    assert progress.calls[1][1] == 1
    assert "charlie | kalan" in progress.calls[1][2]


def test_analyze_progress_handler_batches_updates() -> None:
    scan_results: list[ScanResult] = []
    progress = ProgressSpy()

    handler = session_helpers.AnalyzeProgressHandler(
        scan_results=scan_results,
        progress=progress,
        task_id=9,
        total_count=3,
        clean_text_fn=lambda text, _max_len: (text or "(none)"),
        batch_size=2,
    )

    handler(ScanResult(mail=MailMeta(uid="1", subject="msg-1"), decision="TUT"))
    handler(ScanResult(mail=MailMeta(uid="2", subject="msg-2"), decision="SIL"))

    assert len(scan_results) == 2
    assert progress.calls[0] == (9, 2, "SIL msg-2")

    handler(ScanResult(mail=MailMeta(uid="3", subject="msg-3"), decision="TUT"))

    assert len(scan_results) == 3
    assert progress.calls[1] == (9, 1, "TUT msg-3")


def test_llm_worker_returns_keep_when_cancelled() -> None:
    cancel_event = threading.Event()
    cancel_event.set()

    worker = session_helpers.LLMWorker(cfg=_make_cfg(), cancel_event=cancel_event)
    idx, result = worker((4, ScanResult(mail=MailMeta(uid="88"), decision="SIL", reason="heuristic:promotion:offer")))

    assert idx == 4
    assert result.decision == "TUT"
    assert result.reason == "cancelled"


def test_llm_worker_passes_fast_reason_and_category(monkeypatch) -> None:
    captured = {}

    def fake_pro_analyze(meta, cfg, backend, fast_reason="", fast_category="", cancel_event=None):
        captured["uid"] = meta.uid
        captured["backend"] = backend
        captured["fast_reason"] = fast_reason
        captured["fast_category"] = fast_category
        return ScanResult(mail=meta, decision="SIL", reason="llm:SIL - ok")

    monkeypatch.setattr(session_helpers, "pro_analyze", fake_pro_analyze)

    worker = session_helpers.LLMWorker(cfg=_make_cfg("ollama"), cancel_event=threading.Event())
    candidate = ScanResult(
        mail=MailMeta(uid="42", subject="x"),
        decision="SIL",
        reason="heuristic:promotion:discount",
    )

    idx, result = worker((2, candidate))

    assert idx == 2
    assert result.decision == "SIL"
    assert captured == {
        "uid": "42",
        "backend": "ollama",
        "fast_reason": "heuristic:promotion:discount",
        "fast_category": "promotion",
    }
