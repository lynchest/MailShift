import pytest
import os
from pathlib import Path
from unittest.mock import patch
from mailshift.utils.paths import get_path

@patch("mailshift.utils.paths.platform.system")
@patch("mailshift.utils.paths.os.environ.get")
@patch("mailshift.utils.paths.os.path.expanduser")
def test_get_path_windows_with_appdata(mock_expanduser, mock_environ_get, mock_system):
    """Test get_path on Windows when APPDATA is set."""
    mock_system.return_value = "Windows"
    mock_environ_get.return_value = "C:\\Users\\TestUser\\AppData\\Roaming"

    result = get_path("test.txt")

    expected = Path("C:\\Users\\TestUser\\AppData\\Roaming") / "MailShift" / "test.txt"
    assert result == expected
    mock_environ_get.assert_called_with("APPDATA", mock_expanduser.return_value)

@patch("mailshift.utils.paths.platform.system")
@patch("mailshift.utils.paths.os.environ.get")
@patch("mailshift.utils.paths.os.path.expanduser")
def test_get_path_windows_without_appdata(mock_expanduser, mock_environ_get, mock_system):
    """Test get_path on Windows when APPDATA is not set (fallback to ~)."""
    mock_system.return_value = "Windows"
    mock_environ_get.return_value = "C:\\Users\\Fallback"
    mock_expanduser.return_value = "C:\\Users\\Fallback"

    result = get_path("test.txt")

    expected = Path("C:\\Users\\Fallback") / "MailShift" / "test.txt"
    assert result == expected
    mock_environ_get.assert_called_with("APPDATA", "C:\\Users\\Fallback")

@patch("mailshift.utils.paths.platform.system")
@patch("mailshift.utils.paths.Path.home")
def test_get_path_darwin(mock_home, mock_system):
    """Test get_path on macOS."""
    mock_system.return_value = "Darwin"
    mock_home.return_value = Path("/Users/TestUser")

    result = get_path("test.txt")

    expected = Path("/Users/TestUser") / "Library" / "Application Support" / "MailShift" / "test.txt"
    assert result == expected

@patch("mailshift.utils.paths.platform.system")
@patch("mailshift.utils.paths.Path.home")
def test_get_path_linux(mock_home, mock_system):
    """Test get_path on Linux."""
    mock_system.return_value = "Linux"
    mock_home.return_value = Path("/home/testuser")

    result = get_path("test.txt")

    expected = Path("/home/testuser") / ".config" / "MailShift" / "test.txt"
    assert result == expected

@patch("mailshift.utils.paths.platform.system")
@patch("mailshift.utils.paths.Path.home")
def test_get_path_other_os(mock_home, mock_system):
    """Test get_path on other/unknown OS (defaults to Linux behavior)."""
    mock_system.return_value = "FreeBSD"
    mock_home.return_value = Path("/home/testuser")

    result = get_path("test.txt")

    expected = Path("/home/testuser") / ".config" / "MailShift" / "test.txt"
    assert result == expected
