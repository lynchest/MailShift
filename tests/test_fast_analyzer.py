import re
import pytest
from unittest.mock import patch
from models import MailMeta
import fast_analyzer


@pytest.fixture
def mock_patterns():
    with patch("fast_analyzer.WHITELIST_PATTERN", re.compile("important|urgent", re.IGNORECASE)), \
         patch("fast_analyzer.JUNK_PATTERN", re.compile("discount|offer", re.IGNORECASE)):
        yield


def test_fast_analyze_attachment():
    meta = MailMeta(uid="1", subject="Discount offer", sender="spam@domain.com", has_attachment=True)
    # Should return TUT despite the "discount" because attachment has priority
    result = fast_analyzer.fast_analyze(meta)
    assert result.decision == "TUT"
    assert "attachment" in result.reason.lower()


def test_fast_analyze_whitelist(mock_patterns):
    meta = MailMeta(uid="1", subject="An important offer for you", sender="boss@company.com")
    # Both whitelist ("important") and junk ("offer") are present
    # whitelist should win because it is checked first
    result = fast_analyzer.fast_analyze(meta)
    assert result.decision == "TUT"
    assert "whitelist:important" in result.reason


def test_fast_analyze_junk(mock_patterns):
    meta = MailMeta(uid="1", subject="Special discount inside!", sender="promo@spam.com")
    result = fast_analyzer.fast_analyze(meta)
    assert result.decision == "SIL"
    assert "heuristic:discount" in result.reason


def test_fast_analyze_no_match(mock_patterns):
    meta = MailMeta(uid="1", subject="Hello friend", sender="friend@example.com")
    result = fast_analyzer.fast_analyze(meta)
    assert result.decision == "TUT"
    assert result.reason == "no match"


def test_fast_analyze_whitelist_priority_over_other_rules():
    with patch("fast_analyzer.WHITELIST_PATTERN", re.compile("important", re.IGNORECASE)), \
         patch("fast_analyzer.JUNK_PATTERN", re.compile("offer|no-reply", re.IGNORECASE)):
        meta = MailMeta(
            uid="100",
            subject="Important verification code",
            sender="no-reply@example.com",
            body_preview="Your verification code is 123456",
        )
        result = fast_analyzer.fast_analyze(meta)
        assert result.decision == "TUT"
        assert result.reason == "whitelist:important"


@patch("fast_analyzer.WHITELIST_PATTERN", None)
@patch("fast_analyzer.JUNK_PATTERN", re.compile("no-reply|offer", re.IGNORECASE))
def test_fast_analyze_premium_expiry_is_kept():
    meta = MailMeta(
        uid="101",
        subject="YouTube Premium: Deneme sureniz bitiyor",
        sender="no-reply@youtube.com",
        body_preview="Premium uyeliginiz 3 gun icinde bitecek.",
    )
    result = fast_analyzer.fast_analyze(meta)
    assert result.decision == "TUT"
    assert result.reason.startswith("safe-guard:")


@patch("fast_analyzer.WHITELIST_PATTERN", None)
@patch("fast_analyzer.JUNK_PATTERN", re.compile("no-reply|offer", re.IGNORECASE))
def test_fast_analyze_verification_code_is_kept():
    meta = MailMeta(
        uid="102",
        subject="Dogrulama kodu: 927481",
        sender="no-reply@discord.com",
        body_preview="Bu kod 10 dakika icinde gecerlidir.",
    )
    result = fast_analyzer.fast_analyze(meta)
    assert result.decision == "TUT"
    assert result.reason.startswith("safe-guard:")


@patch("fast_analyzer.WHITELIST_PATTERN", None)
@patch("fast_analyzer.JUNK_PATTERN", re.compile("no-reply|offer", re.IGNORECASE))
def test_fast_analyze_drive_storage_alert_is_kept():
    meta = MailMeta(
        uid="103",
        subject="Google Drive depolama alani dolmak uzere",
        sender="no-reply@google.com",
        body_preview="Depolama alaninizin %95'i dolu.",
    )
    result = fast_analyzer.fast_analyze(meta)
    assert result.decision == "TUT"
    assert result.reason.startswith("safe-guard:")


@patch("fast_analyzer.WHITELIST_PATTERN", None)
@patch("fast_analyzer.JUNK_PATTERN", re.compile("no-reply|noreply", re.IGNORECASE))
def test_fast_analyze_noreply_sender_not_flagged():
    """Blacklist must not match 'no-reply' tokens that only appear in the sender address."""
    meta = MailMeta(
        uid="104",
        subject="Your order has been confirmed",
        sender="no-reply@shop.example.com",
        body_preview="Thank you for your purchase.",
    )
    result = fast_analyzer.fast_analyze(meta)
    assert result.decision == "TUT"
    assert result.reason == "no match"


@patch("fast_analyzer.WHITELIST_PATTERN", None)
@patch("fast_analyzer.JUNK_PATTERN", re.compile("indirim", re.IGNORECASE))
def test_fast_analyze_turkish_dotted_i_normalized():
    """Turkish İ (U+0130) must be folded to 'i' before matching."""
    meta = MailMeta(
        uid="105",
        subject="İndirim Fırsatları",
        sender="promo@shop.com",
        body_preview="",
    )
    result = fast_analyzer.fast_analyze(meta)
    assert result.decision == "SIL"
    assert result.reason.startswith("heuristic:")
