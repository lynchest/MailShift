import pytest
from models import MailMeta, ScanResult, ScanStats


def test_mail_meta_defaults():
    mail = MailMeta(uid="123")
    assert mail.uid == "123"
    assert mail.subject == ""
    assert mail.sender == ""
    assert mail.has_attachment is False


def test_scan_result_initialization():
    mail = MailMeta(uid="123")
    result = ScanResult(mail=mail)
    assert result.mail == mail
    assert result.decision == "TUT"
    assert result.reason == ""

    result2 = ScanResult(mail=mail, decision="SIL", reason="has offer")
    assert result2.decision == "SIL"
    assert result2.reason == "has offer"


def test_scan_stats_error_list_isolation():
    stats1 = ScanStats()
    stats1.errors.append("Error 1")

    stats2 = ScanStats()
    assert len(stats2.errors) == 0  # Should not share the same list
    assert "Error 1" not in stats2.errors


def test_scan_stats_space_saved_mb():
    stats = ScanStats()
    # 10 MB = 10 * 1024 * 1024 bytes
    stats.marked_size_bytes = 10 * 1024 * 1024
    assert stats.space_saved_mb == 10.0

    stats.marked_size_bytes = 5 * 1024 * 1024
    assert stats.space_saved_mb == 5.0
