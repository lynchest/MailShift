from unittest.mock import MagicMock, patch

from mailshift.ui.cli import install_lm_studio, install_ollama


@patch("mailshift.ui.cli.sys.platform", "darwin")
@patch("mailshift.ui.cli.shutil.which", return_value="/opt/homebrew/bin/brew")
@patch("mailshift.ui.cli.subprocess.Popen")
def test_install_ollama_uses_brew_on_macos(mock_popen, _mock_which):
    process = MagicMock()
    process.returncode = 0
    mock_popen.return_value = process

    result = install_ollama()

    assert result is True
    mock_popen.assert_called_once()
    assert mock_popen.call_args[0][0] == ["brew", "install", "ollama"]


@patch("mailshift.ui.cli.sys.platform", "darwin")
@patch("mailshift.ui.cli.shutil.which", return_value="/opt/homebrew/bin/brew")
@patch("mailshift.ui.cli.subprocess.Popen")
def test_install_lm_studio_uses_brew_cask_on_macos(mock_popen, _mock_which):
    process = MagicMock()
    process.returncode = 0
    mock_popen.return_value = process

    result = install_lm_studio()

    assert result is True
    mock_popen.assert_called_once()
    assert mock_popen.call_args[0][0] == ["brew", "install", "--cask", "lm-studio"]


@patch("mailshift.ui.cli.sys.platform", "linux")
@patch("mailshift.ui.cli.console")
def test_install_ollama_returns_false_on_linux(mock_console):
    result = install_ollama()
    assert result is False
    assert mock_console.print.called


@patch("mailshift.ui.cli.sys.platform", "linux")
@patch("mailshift.ui.cli.console")
def test_install_lm_studio_returns_false_on_linux(mock_console):
    result = install_lm_studio()
    assert result is False
    assert mock_console.print.called
