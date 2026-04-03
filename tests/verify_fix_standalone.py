import sys
from unittest.mock import patch, MagicMock

# Create a robust mocking environment to bypass dependency-related import errors
class MockPackage(MagicMock):
    @classmethod
    def __getattr__(cls, name):
        return MagicMock()

# Pre-mock all external and potentially problematic dependencies
modules_to_mock = [
    "rich", "rich.panel", "rich.progress", "rich.prompt", "rich.table", "rich.box",
    "requests", "requests.adapters", "requests.exceptions",
    "psutil", "bs4", "keyring", "keyring.errors",
    "mailshift.core.analyzers.pro", "mailshift.config.config", "mailshift.ui.styles", "mailshift.utils.paths"
]

for mod in modules_to_mock:
    sys.modules[mod] = MockPackage()

# Also mock 'shutil' and 'subprocess' in the cli module if needed, but we'll use patch for those.

# Now we can import the function we want to test.
# Since we are mocking mailshift.*, we need to ensure the import from src.mailshift.ui.cli works.
# Let's mock the internal imports inside cli.py by patching them before they are executed if possible,
# or just rely on the fact that we've already populated sys.modules.

try:
    # We might need to add 'src' to sys.path if it's not there, but PYTHONPATH should handle it.
    from mailshift.ui.cli import install_ollama
except ImportError as e:
    print(f"Import failed: {e}")
    # Fallback: try direct import if the above fails
    sys.path.append("src")
    from mailshift.ui.cli import install_ollama

def test_install_ollama_winget_fix():
    print("Testing install_ollama winget fix on Windows...")

    with patch("mailshift.ui.cli.sys.platform", "win32"), \
         patch("mailshift.ui.cli.shutil.which", return_value="/path/to/winget"), \
         patch("mailshift.ui.cli.subprocess.Popen") as mock_popen, \
         patch("mailshift.ui.cli.console") as mock_console:

        mock_process = MagicMock()
        mock_process.wait.return_value = None
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        result = install_ollama()

        assert result is True
        mock_popen.assert_called_once()
        args, _ = mock_popen.call_args
        expected_cmd = [
            "winget", "install", "--id", "Ollama.Ollama", "-e",
            "--accept-package-agreements", "--accept-source-agreements",
        ]
        assert args[0] == expected_cmd
        print("✓ Verified winget command used.")

def test_install_ollama_no_winget_fallback():
    print("Testing install_ollama fallback when winget is missing...")

    with patch("mailshift.ui.cli.sys.platform", "win32"), \
         patch("mailshift.ui.cli.shutil.which", return_value=None), \
         patch("mailshift.ui.cli.console") as mock_console:

        result = install_ollama()

        assert result is False
        printed_messages = [str(call.args[0]) for call in mock_console.print.call_args_list]
        assert any("https://ollama.com" in msg for msg in printed_messages)
        assert any("winget bulunamadı" in msg for msg in printed_messages)
        print("✓ Verified fallback message.")

if __name__ == "__main__":
    try:
        test_install_ollama_winget_fix()
        test_install_ollama_no_winget_fallback()
        print("\nAll tests passed!")
    except Exception as e:
        print(f"\nTest failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
