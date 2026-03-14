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
