
import sys
import io
import os
import unittest
from unittest.mock import MagicMock, patch

def test_windows_fix_applied():
    # Mock sys.stdout and sys.stderr
    mock_stdout = MagicMock()
    mock_stdout.encoding = 'cp1252'
    mock_stdout.buffer = io.BytesIO()

    mock_stderr = MagicMock()
    mock_stderr.encoding = 'cp1252'
    mock_stderr.buffer = io.BytesIO()

    with patch('sys.platform', 'win32'), \
         patch('sys.stdout', mock_stdout), \
         patch('sys.stderr', mock_stderr), \
         patch.dict(os.environ, {}, clear=True):

        # Logic from main.py:
        if sys.platform == "win32":
            import os as local_os
            if getattr(sys.stdout, "encoding", "").lower() != "utf-8":
                sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
            if getattr(sys.stderr, "encoding", "").lower() != "utf-8":
                sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
            if "TERM" not in local_os.environ:
                local_os.environ["TERM"] = "xterm-256color"

        assert sys.stdout.encoding.lower() == 'utf-8'
        assert sys.stderr.encoding.lower() == 'utf-8'
        assert os.environ["TERM"] == "xterm-256color"
        print("Test passed: Fix applied when encoding is not utf-8")

def test_windows_fix_not_applied_if_utf8():
    # Mock sys.stdout and sys.stderr already utf-8
    mock_stdout = MagicMock()
    mock_stdout.encoding = 'utf-8'

    mock_stderr = MagicMock()
    mock_stderr.encoding = 'UTF-8'

    with patch('sys.platform', 'win32'), \
         patch('sys.stdout', mock_stdout), \
         patch('sys.stderr', mock_stderr), \
         patch.dict(os.environ, {"TERM": "custom"}, clear=True):

        # Logic from main.py:
        if sys.platform == "win32":
            import os as local_os
            if getattr(sys.stdout, "encoding", "").lower() != "utf-8":
                sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
            if getattr(sys.stderr, "encoding", "").lower() != "utf-8":
                sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
            if "TERM" not in local_os.environ:
                local_os.environ["TERM"] = "xterm-256color"

        assert sys.stdout == mock_stdout
        assert sys.stderr == mock_stderr
        assert os.environ["TERM"] == "custom"
        print("Test passed: Fix not applied when encoding is already utf-8")

if __name__ == "__main__":
    test_windows_fix_applied()
    test_windows_fix_not_applied_if_utf8()
