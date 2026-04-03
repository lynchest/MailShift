import sys
from unittest.mock import patch, MagicMock

# Mock dependencies before importing mailshift.ui.cli
mock_rich = MagicMock()
mock_requests = MagicMock()
mock_psutil = MagicMock()
mock_bs4 = MagicMock()

sys.modules["rich"] = mock_rich
sys.modules["rich.panel"] = mock_rich.panel
sys.modules["rich.progress"] = mock_rich.progress
sys.modules["rich.prompt"] = mock_rich.prompt
sys.modules["rich.table"] = mock_rich.table
sys.modules["rich.box"] = mock_rich.box
sys.modules["requests"] = mock_requests
sys.modules["psutil"] = mock_psutil
sys.modules["bs4"] = mock_bs4

import pytest

# Mock internal package imports
sys.modules["mailshift.config.config"] = MagicMock()
sys.modules["mailshift.ui.styles"] = MagicMock()
sys.modules["mailshift.utils.paths"] = MagicMock()

from mailshift.ui.cli import install_ollama

def test_install_ollama_subprocess_no_shell():
    """
    Verify that install_ollama calls subprocess.Popen without shell=True.
    This is a security best practice to avoid shell injection vulnerabilities.
    """
    # Mock sys.platform to be win32 to enter the installation block
    with patch("mailshift.ui.cli.sys.platform", "win32"), \
         patch("mailshift.ui.cli.shutil.which", return_value="/path/to/winget"), \
         patch("mailshift.ui.cli.subprocess.Popen") as mock_popen, \
         patch("mailshift.ui.cli.console") as mock_console, \
         patch("mailshift.ui.cli.show_ollama_next_steps") as mock_show_steps:

        # Mock the process returned by Popen
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        # Execute the function
        result = install_ollama()

        # Assertions
        assert result is True
        mock_popen.assert_called_once()

        args, kwargs = mock_popen.call_args

        # Check shell parameter
        assert kwargs.get("shell") is not True, "subprocess.Popen called with shell=True"

        # Verify the command list
        expected_cmd = [
            "winget", "install", "--id", "Ollama.Ollama", "-e",
            "--accept-package-agreements", "--accept-source-agreements",
        ]
        assert args[0] == expected_cmd


def test_install_ollama_windows_fallback_asks_and_runs_powershell():
    with patch("mailshift.ui.cli.sys.platform", "win32"), \
         patch("mailshift.ui.cli.shutil.which", return_value=None), \
         patch("mailshift.ui.cli.Confirm.ask", return_value=True), \
         patch("mailshift.ui.cli.subprocess.Popen") as mock_popen, \
         patch("mailshift.ui.cli.console"):

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        result = install_ollama()

        assert result is True
        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        assert kwargs.get("shell") is not True
        assert args[0] == [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            "Invoke-Expression (Invoke-RestMethod 'https://ollama.com/install.ps1')",
        ]


def test_install_ollama_windows_fallback_decline_returns_false():
    with patch("mailshift.ui.cli.sys.platform", "win32"), \
         patch("mailshift.ui.cli.shutil.which", return_value=None), \
         patch("mailshift.ui.cli.Confirm.ask", return_value=False), \
         patch("mailshift.ui.cli.subprocess.Popen") as mock_popen, \
         patch("mailshift.ui.cli.console") as mock_console:

        result = install_ollama()

        assert result is False
        mock_popen.assert_not_called()
        printed_messages = [str(call.args[0]) for call in mock_console.print.call_args_list]
        assert any("winget bulunamadı" in msg for msg in printed_messages)
        assert any("internetten indirilen bir PowerShell betiğini çalıştırır" in msg for msg in printed_messages)
        assert any("Otomatik kurulum iptal edildi" in msg for msg in printed_messages)
        manual_install_url = "https://ollama.com"
        assert any(manual_install_url in msg for msg in printed_messages)
