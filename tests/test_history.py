import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

import history
from models import MailMeta, ScanResult, ScanStats


@pytest.fixture
def mock_logs_dir(tmp_path):
    with patch("history.Path") as path_mock:
        # Patch Path so Path('logs') returns our temp dir
        # But we must be careful since Path is used in normal execution
        pass

    # A simpler way to test save_cleanup_log without mocking Path entirely:
    pass


def test_save_cleanup_log(monkeypatch, tmp_path):
    # Monkeypatch history.Path so that when it's called with "logs", it returns tmp_path / "logs"
    original_path = history.Path

    def mock_path(*args, **kwargs):
        if args and args[0] == "logs":
            return tmp_path / "logs"
        return original_path(*args, **kwargs)

    monkeypatch.setattr(history, "Path", mock_path)

    stats = ScanStats(
        total_scanned=10,
        marked_for_deletion=5,
        total_size_bytes=1024 * 1024 * 2,
        marked_size_bytes=1024 * 1024 * 1,
    )

    mail1 = MailMeta(uid="1", subject="Subj", sender="Sender", size_bytes=1024, date="2023")
    results = [
        ScanResult(mail=mail1, decision="SIL", reason="Spam"),
    ]

    log_file_path = history.save_cleanup_log(
        deleted_results=results,
        stats=stats,
        provider="gmail",
        mode="pro",
    )

    path = original_path(log_file_path)
    assert path.exists()
    assert path.parent == (tmp_path / "logs")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["provider"] == "gmail"
    assert data["mode"] == "pro"
    assert data["stats"]["total_scanned"] == 10
    assert data["stats"]["deleted_count"] == 1
    assert data["stats"]["space_saved_mb"] == 1.0


def test_export_scan_results_json(tmp_path):
    output_path = tmp_path / "results.json"
    
    mail1 = MailMeta(uid="1", subject="Subj", sender="Sender", size_bytes=1024, date="2023")
    results = [
        ScanResult(mail=mail1, decision="SIL", reason="Spam"),
    ]

    history.export_scan_results(results, str(output_path))

    assert output_path.exists()
    with open(output_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    assert len(data) == 1
    assert data[0]["uid"] == "1"
    assert data[0]["decision"] == "SIL"


def test_export_scan_results_csv(tmp_path):
    output_path = tmp_path / "results.csv"
    
    mail1 = MailMeta(uid="1", subject="A subject", sender="A sender", size_bytes=1024, date="2023")
    results = [
        ScanResult(mail=mail1, decision="SIL", reason="Spam"),
    ]

    history.export_scan_results(results, str(output_path))

    assert output_path.exists()
    with open(output_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    assert len(lines) == 2  # header + 1 row
    assert "uid,sender,subject,date,size_bytes,decision,reason" in lines[0]
    assert "1,A sender,A subject,2023,1024,SIL,Spam" in lines[1]
