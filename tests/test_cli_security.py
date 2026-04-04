from unittest.mock import MagicMock, patch

from mailshift.ui.cli import install_ollama

def test_install_ollama_subprocess_no_shell():
    """
    Verify that install_ollama calls subprocess.Popen without shell=True.
    This is a security best practice to avoid shell injection vulnerabilities.
    """
    # Mock sys.platform to be win32 to enter the installation block
    with patch("mailshift.ui.cli.sys.platform", "win32"), \
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
        expected_cmd = ["powershell", "-Command", "irm https://ollama.com/install.ps1 | iex"]
        assert args[0] == expected_cmd
