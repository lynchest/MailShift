import pytest
from concurrent.futures import Future
from unittest.mock import patch

from mailshift.config.config import Mode
from mailshift.models.models import MailMeta, ScanResult
from mailshift.main import clean_text, format_duration
from mailshift.main import main as main_command
from mailshift.utils.hardware import WorkerPlan


@pytest.fixture(autouse=True)
def _isolate_power_probe_preference(monkeypatch):
    """Keep main tests deterministic regardless of persisted local settings."""
    monkeypatch.setattr("mailshift.main.get_worker_probe_preference", lambda: None)


# ---------------------------------------------------------------------------
# clean_text helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "input_text, expected",
    [
        # Empty/None
        (None, "(bilinmiyor)"),
        ("", "(bilinmiyor)"),
        ("   ", "(bilinmiyor)"),

        # Normal strings
        ("Hello World", "Hello World"),
        ("Normal text here", "Normal text here"),

        # Strings with control characters
        ("Hello\x00World", "Hello World"),
        ("Hello\nWorld", "Hello World"),
        ("Text\twith\ttabs", "Text with tabs"),
        ("Newline\r\nand\rreturn", "Newline and return"),

        # Strings with Turkish characters/accents
        # NOTE: unicodedata.normalize("NFKC", text) does not remove accents, it normalizes characters.
        ("İstanbul", "İstanbul"),
        ("ÇĞIÖŞÜ çğıöşü", "ÇĞIÖŞÜ çğıöşü"),
        ("Café", "Café"),

        # Extra whitespace
        ("  Too   much   spaces  ", "Too much spaces"),

        # Long strings
        ("This is a very long string that should be truncated", "This is a very long string that sho…"),
        ("This is exactly 35 chars long text!", "This is exactly 35 chars long text!"),

        # Control chars that leave nothing
        ("\x00\x01\n\r", "(bilinmiyor)"),
    ],
)
def test_clean_text(input_text, expected):
    """Test clean_text behavior."""
    assert clean_text(input_text, max_len=35) == expected


def test_clean_text_custom_max_len():
    """Test clean_text with a custom max_len."""
    assert clean_text("Hello World", max_len=5) == "Hello…"
    assert clean_text("Hello World", max_len=20) == "Hello World"


# ---------------------------------------------------------------------------
# format_duration helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "seconds, expected",
    [
        # Zero and negatives
        (0, "~0 sn"),
        (0.0, "~0 sn"),
        (-5.5, "~0 sn"),

        # Seconds only (< 60)
        (5, "~5 sn"),
        (45.4, "~45 sn"),
        (45.5, "~46 sn"),
        (59.9, "~1 dk 00 sn"),

        # Exact minutes
        (60, "~1 dk 00 sn"),
        (120, "~2 dk 00 sn"),

        # Minutes and seconds
        (65, "~1 dk 05 sn"),
        (125, "~2 dk 05 sn"),
        (61.2, "~1 dk 01 sn"),
        (3599, "~59 dk 59 sn"),

        # Hours+ (function just returns total minutes)
        (3600, "~60 dk 00 sn"),
        (3665, "~61 dk 05 sn"),
    ],
)
def test_format_duration(seconds, expected):
    """Test format_duration behavior."""
    assert format_duration(seconds) == expected


def _call_main_callback(**overrides):
    params = {
        "provider": "gmail",
        "mode": None,
        "username": "user@gmail.com",
        "password": "secret",
        "host": None,
        "port": None,
        "use_ssl": True,
        "dry_run": True,
        "scan_limit": None,
        "since": None,
        "before": None,
        "ollama_url": "http://localhost:11434",
        "ollama_model": "qwen3.5:2B",
        "ollama_prompt": None,
        "uninstall": False,
        "history": False,
        "add_whitelist": None,
        "remove_whitelist": None,
        "add_blacklist": None,
        "remove_blacklist": None,
        "list_keywords_flag": False,
        "export_file": None,
        "workers": None,
        "power_worker_probe": None,
    }
    params.update(overrides)
    return main_command.callback(**params)


class _EmptyInboxEngine:
    def __init__(self, cfg):
        self.cfg = cfg

    def connect(self):
        return None

    def list_uids(self):
        return []

    def disconnect(self):
        return None


class _SingleMailEngine:
    def __init__(self, cfg):
        self.cfg = cfg

    def connect(self):
        return None

    def list_uids(self):
        return ["1"]

    def fetch_headers_concurrent(self, uids, progress_cb):
        for uid in uids:
            progress_cb(MailMeta(uid=uid, subject="Subject", sender="sender@example.com", size_bytes=128))

    def fetch_body_for_cached_mails(self, mails, progress_cb):
        for mail in mails:
            mail.body_preview = "body"
            progress_cb(mail)

    def disconnect(self):
        return None


class _MultiMailEngine:
    def __init__(self, cfg):
        self.cfg = cfg

    def connect(self):
        return None

    def list_uids(self):
        return [str(i) for i in range(1, 13)]

    def fetch_headers_concurrent(self, uids, progress_cb):
        for uid in uids:
            progress_cb(MailMeta(uid=uid, subject=f"Subject {uid}", sender="sender@example.com", size_bytes=128))

    def fetch_body_for_cached_mails(self, mails, progress_cb):
        for mail in mails:
            mail.body_preview = "body"
            progress_cb(mail)

    def disconnect(self):
        return None


class _SafeLLMWorker:
    def __init__(self, cfg, cancel_event):
        self.cfg = cfg
        self.cancel_event = cancel_event

    def __call__(self, payload):
        idx, candidate = payload
        return idx, ScanResult(mail=candidate.mail, decision="TUT", reason="unit-test")


class _RecordingExecutor:
    last_max_workers = None

    def __init__(self, max_workers):
        self.max_workers = max_workers
        _RecordingExecutor.last_max_workers = max_workers

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def submit(self, fn, *args, **kwargs):
        future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except Exception as exc:
            future.set_exception(exc)
        return future


class _HistoryExecutor:
    history = []

    def __init__(self, max_workers):
        self.max_workers = max_workers
        _HistoryExecutor.history.append(max_workers)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def submit(self, fn, *args, **kwargs):
        future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except Exception as exc:
            future.set_exception(exc)
        return future


class _TimeoutLLMWorker:
    def __init__(self, cfg, cancel_event):
        self.cfg = cfg
        self.cancel_event = cancel_event

    def __call__(self, payload):
        idx, candidate = payload
        return idx, ScanResult(mail=candidate.mail, decision="TUT", reason="llm-timeout")


def _make_worker_plan(
    workers=3,
    requested_workers=None,
    upper_limit=3,
    source="auto",
    reason="auto",
    backend="ollama",
    mode="pro",
    is_effective=True,
    was_clamped=False,
):
    return WorkerPlan(
        workers=workers,
        requested_workers=requested_workers,
        upper_limit=upper_limit,
        source=source,
        reason=reason,
        backend=backend,
        mode=mode,
        is_effective=is_effective,
        was_clamped=was_clamped,
    )


@pytest.mark.parametrize("backend", ["ollama", "lm_studio"])
def test_main_propagates_backend_to_worker_resolution(backend):
    with patch("mailshift.main.prompt_mode", return_value=(Mode.PRO, "qwen3.5:2B", backend, None)), \
         patch("mailshift.main.resolve_worker_plan", return_value=_make_worker_plan(workers=3, backend=backend)) as worker_plan_resolver, \
         patch("mailshift.main.MailEngine", _EmptyInboxEngine), \
         patch("mailshift.main.verify_llm_health", return_value=True), \
         patch("mailshift.main.ensure_proton_bridge_ready", return_value=True), \
         patch("mailshift.main.get_system_info"), \
         patch("mailshift.main.format_system_info", return_value=""), \
         patch("mailshift.main.clear_console"), \
         patch("mailshift.main.console.print"), \
         patch("mailshift.main.cleanup_ollama_if_it_was_started_by_us"), \
         patch("mailshift.main.cleanup_lm_studio_if_it_was_started_by_us"), \
         patch("mailshift.main.close_ollama_session"), \
         patch("mailshift.main.unload_lm_studio_models"):
        _call_main_callback(mode=None, workers=None)

    worker_plan_resolver.assert_called_once_with(
        "qwen3.5:2B",
        "pro",
        manual_workers=None,
        backend=backend,
        power_worker_probe=False,
    )


def test_main_uses_displayed_worker_in_phase2_runtime():
    _RecordingExecutor.last_max_workers = None
    printed_objects = []

    def _capture_print(*args, **kwargs):
        if args:
            printed_objects.append(args[0])

    with patch(
            "mailshift.main.resolve_worker_plan",
            return_value=_make_worker_plan(
                workers=4,
                requested_workers=11,
                upper_limit=4,
                source="manual-clamped",
                reason="manual 11 -> 4",
                backend="ollama",
                mode="pro",
                is_effective=True,
                was_clamped=True,
            ),
        ) as worker_plan_resolver, \
         patch("mailshift.main.MailEngine", _SingleMailEngine), \
         patch("mailshift.main.verify_llm_health", return_value=True), \
         patch("mailshift.main.ensure_proton_bridge_ready", return_value=True), \
         patch("mailshift.main.load_mails_cache_by_uids", return_value=[]), \
         patch("mailshift.main.save_mails_cache"), \
         patch("mailshift.main.fast_analyze", side_effect=lambda mail: ScanResult(mail=mail, decision="SIL", reason="candidate")), \
         patch("mailshift.main.LLMWorker", _SafeLLMWorker), \
         patch("mailshift.main.ThreadPoolExecutor", _RecordingExecutor), \
         patch("mailshift.main.get_system_info"), \
         patch("mailshift.main.format_system_info", return_value=""), \
         patch("mailshift.main.clear_console"), \
         patch("mailshift.main.console.print", side_effect=_capture_print), \
         patch("mailshift.main.cleanup_ollama_if_it_was_started_by_us"), \
         patch("mailshift.main.cleanup_lm_studio_if_it_was_started_by_us"), \
         patch("mailshift.main.close_ollama_session"), \
         patch("mailshift.main.unload_lm_studio_models"):
        _call_main_callback(mode="pro", workers=11)

    worker_plan_resolver.assert_called_once_with(
        "qwen3.5:2B",
        "pro",
        manual_workers=11,
        backend="ollama",
        power_worker_probe=False,
    )
    assert _RecordingExecutor.last_max_workers == 4

    config_panel_text = ""
    for obj in printed_objects:
        panel_text = getattr(obj, "renderable", None)
        if isinstance(panel_text, str) and "Workers:" in panel_text:
            config_panel_text = panel_text
            break

    assert "[bold]Workers:[/bold]  4" in config_panel_text


def test_main_shows_fast_mode_worker_as_not_used():
    printed_objects = []

    def _capture_print(*args, **kwargs):
        if args:
            printed_objects.append(args[0])

    with patch(
            "mailshift.main.resolve_worker_plan",
            return_value=_make_worker_plan(
                workers=8,
                requested_workers=9,
                upper_limit=8,
                source="manual-clamped",
                reason="manual 9 -> 8",
                backend="ollama",
                mode="fast",
                is_effective=False,
                was_clamped=True,
            ),
        ), \
         patch("mailshift.main.MailEngine", _EmptyInboxEngine), \
         patch("mailshift.main.ensure_proton_bridge_ready", return_value=True), \
         patch("mailshift.main.get_system_info"), \
         patch("mailshift.main.format_system_info", return_value=""), \
         patch("mailshift.main.clear_console"), \
         patch("mailshift.main.console.print", side_effect=_capture_print), \
         patch("mailshift.main.cleanup_ollama_if_it_was_started_by_us"), \
         patch("mailshift.main.cleanup_lm_studio_if_it_was_started_by_us"), \
         patch("mailshift.main.close_ollama_session"), \
         patch("mailshift.main.unload_lm_studio_models"):
        _call_main_callback(mode="fast", workers=9)

    config_panel_text = ""
    for obj in printed_objects:
        panel_text = getattr(obj, "renderable", None)
        if isinstance(panel_text, str) and "Workers:" in panel_text:
            config_panel_text = panel_text
            break

    assert "Kullanılmıyor (Fast mode)" in config_panel_text


def test_main_phase2_adaptive_workers_reduce_on_timeout_feedback():
    _HistoryExecutor.history = []

    with patch(
            "mailshift.main.resolve_worker_plan",
            return_value=_make_worker_plan(
                workers=4,
                requested_workers=None,
                upper_limit=4,
                source="auto",
                reason="auto",
                backend="ollama",
                mode="pro",
                is_effective=True,
                was_clamped=False,
            ),
        ), \
         patch("mailshift.main.MailEngine", _MultiMailEngine), \
         patch("mailshift.main.verify_llm_health", return_value=True), \
         patch("mailshift.main.ensure_proton_bridge_ready", return_value=True), \
         patch("mailshift.main.load_mails_cache_by_uids", return_value=[]), \
         patch("mailshift.main.save_mails_cache"), \
         patch("mailshift.main.fast_analyze", side_effect=lambda mail: ScanResult(mail=mail, decision="SIL", reason="candidate")), \
         patch("mailshift.main.LLMWorker", _TimeoutLLMWorker), \
         patch("mailshift.main.ThreadPoolExecutor", _HistoryExecutor), \
         patch("mailshift.main.get_system_info"), \
         patch("mailshift.main.format_system_info", return_value=""), \
         patch("mailshift.main.clear_console"), \
         patch("mailshift.main.console.print"), \
         patch("mailshift.main.cleanup_ollama_if_it_was_started_by_us"), \
         patch("mailshift.main.cleanup_lm_studio_if_it_was_started_by_us"), \
         patch("mailshift.main.close_ollama_session"), \
         patch("mailshift.main.unload_lm_studio_models"):
        _call_main_callback(mode="pro", workers=4)

    assert _HistoryExecutor.history
    assert _HistoryExecutor.history[0] == 4
    assert any(worker_count < 4 for worker_count in _HistoryExecutor.history[1:])


def test_main_prints_clamp_warning_panel_when_manual_workers_capped():
    printed_objects = []

    def _capture_print(*args, **kwargs):
        if args:
            printed_objects.append(args[0])

    with patch(
            "mailshift.main.resolve_worker_plan",
            return_value=_make_worker_plan(
                workers=5,
                requested_workers=20,
                upper_limit=5,
                source="manual-clamped",
                reason="manual 20 -> 5 (safe upper limit=5)",
                backend="lm_studio",
                mode="pro",
                is_effective=True,
                was_clamped=True,
            ),
        ), \
         patch("mailshift.main.MailEngine", _EmptyInboxEngine), \
         patch("mailshift.main.verify_llm_health", return_value=True), \
         patch("mailshift.main.ensure_proton_bridge_ready", return_value=True), \
         patch("mailshift.main.get_system_info"), \
         patch("mailshift.main.format_system_info", return_value=""), \
         patch("mailshift.main.clear_console"), \
         patch("mailshift.main.console.print", side_effect=_capture_print), \
         patch("mailshift.main.cleanup_ollama_if_it_was_started_by_us"), \
         patch("mailshift.main.cleanup_lm_studio_if_it_was_started_by_us"), \
         patch("mailshift.main.close_ollama_session"), \
         patch("mailshift.main.unload_lm_studio_models"):
        _call_main_callback(mode="pro", workers=20)

    found_warning = False
    for obj in printed_objects:
        panel_text = getattr(obj, "renderable", None)
        if isinstance(panel_text, str) and "Manuel worker değeri güvenli üst sınıra çekildi" in panel_text:
            found_warning = True
            break

    assert found_warning is True


def test_main_persists_worker_profile_feedback_for_auto_plan():
    with patch(
            "mailshift.main.resolve_worker_plan",
            return_value=_make_worker_plan(
                workers=4,
                requested_workers=None,
                upper_limit=4,
                source="auto",
                reason="auto",
                backend="ollama",
                mode="pro",
                is_effective=True,
                was_clamped=False,
            ),
        ), \
         patch("mailshift.main.MailEngine", _SingleMailEngine), \
         patch("mailshift.main.verify_llm_health", return_value=True), \
         patch("mailshift.main.ensure_proton_bridge_ready", return_value=True), \
         patch("mailshift.main.load_mails_cache_by_uids", return_value=[]), \
         patch("mailshift.main.save_mails_cache"), \
         patch("mailshift.main.fast_analyze", side_effect=lambda mail: ScanResult(mail=mail, decision="SIL", reason="candidate")), \
         patch("mailshift.main.LLMWorker", _SafeLLMWorker), \
         patch("mailshift.main.ThreadPoolExecutor", _RecordingExecutor), \
         patch("mailshift.main.persist_worker_profile_run", return_value=3) as profile_recorder, \
         patch("mailshift.main.get_system_info"), \
         patch("mailshift.main.format_system_info", return_value=""), \
         patch("mailshift.main.clear_console"), \
         patch("mailshift.main.console.print"), \
         patch("mailshift.main.cleanup_ollama_if_it_was_started_by_us"), \
         patch("mailshift.main.cleanup_lm_studio_if_it_was_started_by_us"), \
         patch("mailshift.main.close_ollama_session"), \
         patch("mailshift.main.unload_lm_studio_models"):
        _call_main_callback(mode="pro", workers=None)

    profile_recorder.assert_called_once()
    assert profile_recorder.call_args.kwargs["mode"] == "pro"
    assert profile_recorder.call_args.kwargs["backend"] == "ollama"


def test_main_uses_saved_power_probe_preference_in_worker_resolution():
    with patch("mailshift.main.get_worker_probe_preference", return_value=True), \
         patch("mailshift.main.resolve_worker_plan", return_value=_make_worker_plan(workers=3, source="auto-probe")) as worker_plan_resolver, \
         patch("mailshift.main.MailEngine", _EmptyInboxEngine), \
         patch("mailshift.main.verify_llm_health", return_value=True), \
         patch("mailshift.main.ensure_proton_bridge_ready", return_value=True), \
         patch("mailshift.main.get_system_info"), \
         patch("mailshift.main.format_system_info", return_value=""), \
         patch("mailshift.main.clear_console"), \
         patch("mailshift.main.console.print"), \
         patch("mailshift.main.cleanup_ollama_if_it_was_started_by_us"), \
         patch("mailshift.main.cleanup_lm_studio_if_it_was_started_by_us"), \
         patch("mailshift.main.close_ollama_session"), \
         patch("mailshift.main.unload_lm_studio_models"):
        _call_main_callback(mode="pro", workers=None)

    assert worker_plan_resolver.call_args.kwargs["power_worker_probe"] is True


def test_main_saves_power_probe_preference_when_cli_flag_is_set():
    with patch("mailshift.main.set_worker_probe_preference", return_value=True) as save_pref, \
         patch("mailshift.main.resolve_worker_plan", return_value=_make_worker_plan(workers=3, source="auto-probe")) as worker_plan_resolver, \
         patch("mailshift.main.MailEngine", _EmptyInboxEngine), \
         patch("mailshift.main.verify_llm_health", return_value=True), \
         patch("mailshift.main.ensure_proton_bridge_ready", return_value=True), \
         patch("mailshift.main.get_system_info"), \
         patch("mailshift.main.format_system_info", return_value=""), \
         patch("mailshift.main.clear_console"), \
         patch("mailshift.main.console.print"), \
         patch("mailshift.main.cleanup_ollama_if_it_was_started_by_us"), \
         patch("mailshift.main.cleanup_lm_studio_if_it_was_started_by_us"), \
         patch("mailshift.main.close_ollama_session"), \
         patch("mailshift.main.unload_lm_studio_models"):
        _call_main_callback(mode="pro", workers=None, power_worker_probe=True)

    save_pref.assert_called_once_with(True)
    assert worker_plan_resolver.call_args.kwargs["power_worker_probe"] is True
