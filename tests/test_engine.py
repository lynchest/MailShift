"""
tests/test_engine.py – Unit tests for heuristic analysis, config helpers,
pro_analyze (mocked Ollama), and MailEngine (mocked IMAP).
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from fast_analyzer import fast_analyze
from pro_analyzer import pro_analyze
from config import (
    AppConfig,
    IMAPConfig,
    Mode,
    OllamaConfig,
    Provider,
    build_imap_config,
)
from engine import MailEngine, MailMeta, ScanResult, ScanStats


# ---------------------------------------------------------------------------
# build_imap_config helpers
# ---------------------------------------------------------------------------


def test_build_imap_config_gmail_defaults():
    cfg = build_imap_config(Provider.GMAIL, "user@gmail.com", "secret")
    assert cfg.host == "imap.gmail.com"
    assert cfg.port == 993
    assert cfg.use_ssl is True
    assert cfg.username == "user@gmail.com"
    assert cfg.password.get_secret_value() == "secret"


def test_build_imap_config_proton_defaults():
    cfg = build_imap_config(Provider.PROTON, "user@proton.me", "bridge-pass")
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 1143
    assert cfg.use_ssl is False


def test_build_imap_config_overrides():
    cfg = build_imap_config(
        Provider.GMAIL, "u", "p", host="custom.host", port=1234, use_ssl=False
    )
    assert cfg.host == "custom.host"
    assert cfg.port == 1234
    assert cfg.use_ssl is False


# ---------------------------------------------------------------------------
# fast_analyze – junk detection
# ---------------------------------------------------------------------------


def _make_meta(subject="", sender="", body="", uid="1", size=1024) -> MailMeta:
    return MailMeta(uid=uid, subject=subject, sender=sender, body_preview=body, size_bytes=size)


def test_fast_analyze_unsubscribe_in_body():
    meta = _make_meta(body="Click here to unsubscribe from our mailing list.")
    result = fast_analyze(meta)
    assert result.decision == "SIL"
    assert "unsubscribe" in result.reason


def test_fast_analyze_newsletter_in_subject():
    meta = _make_meta(subject="Our weekly Newsletter for you!")
    result = fast_analyze(meta)
    assert result.decision == "SIL"


def test_fast_analyze_turkish_keyword():
    meta = _make_meta(subject="Büyük kampanya! Kaçırma!")
    result = fast_analyze(meta)
    assert result.decision == "SIL"
    assert "kampanya" in result.reason


def test_fast_analyze_turkish_unsubscribe():
    meta = _make_meta(body="Abonelikten çık için buraya tıklayın.")
    result = fast_analyze(meta)
    assert result.decision == "SIL"


def test_fast_analyze_whitelist_invoice_overrides_junk():
    """Even if the body has 'unsubscribe', a whitelist keyword should keep it."""
    meta = _make_meta(
        subject="Your invoice #1234",
        body="Please unsubscribe if you don't want updates.",
    )
    result = fast_analyze(meta)
    assert result.decision == "TUT"
    assert "invoice" in result.reason


def test_fast_analyze_whitelist_otp():
    meta = _make_meta(subject="Your OTP code is 123456")
    result = fast_analyze(meta)
    assert result.decision == "TUT"


def test_fast_analyze_whitelist_security():
    meta = _make_meta(subject="Security alert: new login detected")
    result = fast_analyze(meta)
    assert result.decision == "TUT"


def test_fast_analyze_whitelist_order():
    meta = _make_meta(subject="Your order has been shipped")
    result = fast_analyze(meta)
    assert result.decision == "TUT"


def test_fast_analyze_no_match_keeps_mail():
    meta = _make_meta(subject="Hey, are you free this weekend?", body="Let me know!")
    result = fast_analyze(meta)
    assert result.decision == "TUT"
    assert result.reason == "no match"


def test_fast_analyze_promotion_keyword():
    meta = _make_meta(body="Exclusive promotion just for you – 50% off!")
    result = fast_analyze(meta)
    assert result.decision == "SIL"


def test_fast_analyze_case_insensitive():
    meta = _make_meta(subject="UNSUBSCRIBE FROM THIS LIST")
    result = fast_analyze(meta)
    assert result.decision == "SIL"


# ---------------------------------------------------------------------------
# OllamaConfig defaults
# ---------------------------------------------------------------------------


def test_ollama_config_defaults():
    cfg = OllamaConfig()
    assert cfg.base_url == "http://localhost:11434"
    assert cfg.model == "qwen2.5:3b"
    assert cfg.max_body_chars == 500
    assert "SIL" in cfg.system_prompt
    assert "TUT" in cfg.system_prompt


# ---------------------------------------------------------------------------
# AppConfig construction
# ---------------------------------------------------------------------------


def test_app_config_dry_run_default():
    imap = build_imap_config(Provider.GMAIL, "u@g.com", "p")
    cfg = AppConfig(provider=Provider.GMAIL, mode=Mode.FAST, imap=imap)
    assert cfg.dry_run is True


def test_app_config_scan_limit_none_by_default():
    imap = build_imap_config(Provider.GMAIL, "u@g.com", "p")
    cfg = AppConfig(provider=Provider.GMAIL, mode=Mode.FAST, imap=imap)
    assert cfg.scan_limit is None


# ---------------------------------------------------------------------------
# pro_analyze – Ollama LLM (mocked HTTP)
# ---------------------------------------------------------------------------


def _make_ollama_cfg() -> OllamaConfig:
    return OllamaConfig(base_url="http://localhost:11434", model="qwen2.5:3b")


def _mock_ollama_response(response_text: str):
    """Helper that creates a mocked requests.Response."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"response": response_text}
    return mock_resp


def test_pro_analyze_sil_decision():
    meta = _make_meta(subject="Big sale newsletter!", body="Unsubscribe here")
    with patch("pro_analyzer.requests.post", return_value=_mock_ollama_response("SIL")) as mock_post:
        result = pro_analyze(meta, _make_ollama_cfg())
    assert result.decision == "SIL"
    assert result.reason == "llm:SIL"
    mock_post.assert_called_once()


def test_pro_analyze_tut_decision():
    meta = _make_meta(subject="Meeting notes", body="Please review the attached agenda.")
    with patch("pro_analyzer.requests.post", return_value=_mock_ollama_response("TUT")) as mock_post:
        result = pro_analyze(meta, _make_ollama_cfg())
    assert result.decision == "TUT"
    assert result.reason == "llm:TUT"


def test_pro_analyze_falls_back_to_tut_on_error():
    """Any network / API error must default to TUT (keep) to protect important mail."""
    meta = _make_meta(subject="Important update")
    with patch("pro_analyzer.requests.post", side_effect=requests.ConnectionError("refused")):
        result = pro_analyze(meta, _make_ollama_cfg())
    assert result.decision == "TUT"
    assert "llm-error" in result.reason


def test_pro_analyze_truncates_body_to_max_chars():
    """Body preview sent to LLM must not exceed max_body_chars."""
    long_body = "x" * 2000
    meta = _make_meta(body=long_body)
    cfg = OllamaConfig(max_body_chars=500)
    captured_payload = {}

    def capture_post(url, json, timeout):
        captured_payload.update(json)
        return _mock_ollama_response("TUT")

    with patch("pro_analyzer.requests.post", side_effect=capture_post):
        pro_analyze(meta, cfg)

    assert len(captured_payload.get("prompt", "")) <= 500


# ---------------------------------------------------------------------------
# MailEngine – analyze pipeline (no real IMAP)
# ---------------------------------------------------------------------------


def _make_app_cfg(mode: Mode = Mode.FAST, dry_run: bool = True) -> AppConfig:
    imap = build_imap_config(Provider.GMAIL, "u@g.com", "p")
    return AppConfig(provider=Provider.GMAIL, mode=mode, imap=imap, dry_run=dry_run)


def test_engine_analyze_fast_mode():
    cfg = _make_app_cfg(mode=Mode.FAST)
    engine = MailEngine(cfg)
    mails = [
        _make_meta(uid="1", subject="Newsletter", body="Unsubscribe here", size=512),
        _make_meta(uid="2", subject="Hello friend", body="Let's catch up", size=256),
    ]
    results, stats = engine.analyze(mails)

    assert stats.total_scanned == 2
    assert stats.marked_for_deletion == 1
    assert stats.marked_size_bytes == 512

    decisions = {r.mail.uid: r.decision for r in results}
    assert decisions["1"] == "SIL"
    assert decisions["2"] == "TUT"


def test_engine_analyze_fast_mode_progress_callback():
    cfg = _make_app_cfg(mode=Mode.FAST)
    engine = MailEngine(cfg)
    mails = [
        _make_meta(uid="1", body="unsubscribe"),
        _make_meta(uid="2", body="Hello"),
    ]
    seen: list[ScanResult] = []
    results, stats = engine.analyze(mails, progress_cb=seen.append)
    assert len(seen) == 2


def test_engine_analyze_pro_mode_uses_llm_for_flagged():
    """In Pro mode, emails flagged by heuristic should be sent to the LLM."""
    cfg = _make_app_cfg(mode=Mode.PRO)
    engine = MailEngine(cfg)
    mails = [_make_meta(uid="1", body="unsubscribe – big sale!")]

    with patch("pro_analyzer.requests.post", return_value=_mock_ollama_response("SIL")):
        results, stats = engine.analyze(mails)

    assert stats.marked_for_deletion == 1
    assert results[0].decision == "SIL"


def test_engine_analyze_pro_mode_llm_overrides_heuristic_tut():
    """In Pro mode, if LLM says TUT even for heuristic-flagged mail, keep it."""
    cfg = _make_app_cfg(mode=Mode.PRO)
    engine = MailEngine(cfg)
    mails = [_make_meta(uid="1", body="unsubscribe")]

    with patch("pro_analyzer.requests.post", return_value=_mock_ollama_response("TUT")):
        results, stats = engine.analyze(mails)

    assert stats.marked_for_deletion == 0
    assert results[0].decision == "TUT"


def test_engine_analyze_pro_mode_clean_mail_skips_llm():
    """In Pro mode, mail that passes heuristics should NOT call the LLM."""
    cfg = _make_app_cfg(mode=Mode.PRO)
    engine = MailEngine(cfg)
    mails = [_make_meta(uid="1", subject="Hello", body="Let's meet tomorrow.")]

    with patch("pro_analyzer.requests.post") as mock_post:
        results, stats = engine.analyze(mails)

    mock_post.assert_not_called()
    assert results[0].decision == "TUT"


def test_scan_stats_space_saved_mb():
    stats = ScanStats(marked_size_bytes=2 * 1024 * 1024)
    assert stats.space_saved_mb == pytest.approx(2.0)



# ---------------------------------------------------------------------------
# build_imap_config helpers
# ---------------------------------------------------------------------------


def test_build_imap_config_gmail_defaults():
    cfg = build_imap_config(Provider.GMAIL, "user@gmail.com", "secret")
    assert cfg.host == "imap.gmail.com"
    assert cfg.port == 993
    assert cfg.use_ssl is True
    assert cfg.username == "user@gmail.com"
    assert cfg.password.get_secret_value() == "secret"


def test_build_imap_config_proton_defaults():
    cfg = build_imap_config(Provider.PROTON, "user@proton.me", "bridge-pass")
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 1143
    assert cfg.use_ssl is False


def test_build_imap_config_overrides():
    cfg = build_imap_config(
        Provider.GMAIL, "u", "p", host="custom.host", port=1234, use_ssl=False
    )
    assert cfg.host == "custom.host"
    assert cfg.port == 1234
    assert cfg.use_ssl is False


# ---------------------------------------------------------------------------
# fast_analyze – junk detection
# ---------------------------------------------------------------------------


def _make_meta(subject="", sender="", body="", uid="1", size=1024) -> MailMeta:
    return MailMeta(uid=uid, subject=subject, sender=sender, body_preview=body, size_bytes=size)


def test_fast_analyze_unsubscribe_in_body():
    meta = _make_meta(body="Click here to unsubscribe from our mailing list.")
    result = fast_analyze(meta)
    assert result.decision == "SIL"
    assert "unsubscribe" in result.reason


def test_fast_analyze_newsletter_in_subject():
    meta = _make_meta(subject="Our weekly Newsletter for you!")
    result = fast_analyze(meta)
    assert result.decision == "SIL"


def test_fast_analyze_turkish_keyword():
    meta = _make_meta(subject="Büyük kampanya! Kaçırma!")
    result = fast_analyze(meta)
    assert result.decision == "SIL"
    assert "kampanya" in result.reason


def test_fast_analyze_turkish_unsubscribe():
    meta = _make_meta(body="Abonelikten çık için buraya tıklayın.")
    result = fast_analyze(meta)
    assert result.decision == "SIL"


def test_fast_analyze_whitelist_invoice_overrides_junk():
    """Even if the body has 'unsubscribe', a whitelist keyword should keep it."""
    meta = _make_meta(
        subject="Your invoice #1234",
        body="Please unsubscribe if you don't want updates.",
    )
    result = fast_analyze(meta)
    assert result.decision == "TUT"
    assert "invoice" in result.reason


def test_fast_analyze_whitelist_otp():
    meta = _make_meta(subject="Your OTP code is 123456")
    result = fast_analyze(meta)
    assert result.decision == "TUT"


def test_fast_analyze_whitelist_security():
    meta = _make_meta(subject="Security alert: new login detected")
    result = fast_analyze(meta)
    assert result.decision == "TUT"


def test_fast_analyze_whitelist_order():
    meta = _make_meta(subject="Your order has been shipped")
    result = fast_analyze(meta)
    assert result.decision == "TUT"


def test_fast_analyze_no_match_keeps_mail():
    meta = _make_meta(subject="Hey, are you free this weekend?", body="Let me know!")
    result = fast_analyze(meta)
    assert result.decision == "TUT"
    assert result.reason == "no match"


def test_fast_analyze_promotion_keyword():
    meta = _make_meta(body="Exclusive promotion just for you – 50% off!")
    result = fast_analyze(meta)
    assert result.decision == "SIL"


def test_fast_analyze_case_insensitive():
    meta = _make_meta(subject="UNSUBSCRIBE FROM THIS LIST")
    result = fast_analyze(meta)
    assert result.decision == "SIL"


# ---------------------------------------------------------------------------
# OllamaConfig defaults
# ---------------------------------------------------------------------------


def test_ollama_config_defaults():
    cfg = OllamaConfig()
    assert cfg.base_url == "http://localhost:11434"
    assert cfg.model == "qwen2.5:3b"
    assert cfg.max_body_chars == 500
    assert "SIL" in cfg.system_prompt
    assert "TUT" in cfg.system_prompt


# ---------------------------------------------------------------------------
# AppConfig construction
# ---------------------------------------------------------------------------


def test_app_config_dry_run_default():
    imap = build_imap_config(Provider.GMAIL, "u@g.com", "p")
    cfg = AppConfig(provider=Provider.GMAIL, mode=Mode.FAST, imap=imap)
    assert cfg.dry_run is True


def test_app_config_scan_limit_none_by_default():
    imap = build_imap_config(Provider.GMAIL, "u@g.com", "p")
    cfg = AppConfig(provider=Provider.GMAIL, mode=Mode.FAST, imap=imap)
    assert cfg.scan_limit is None
