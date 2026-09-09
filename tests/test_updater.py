from unittest.mock import MagicMock, patch
from mailshift.utils.updater import (
    _parse_version,
    _get_installed_version,
    _get_pypi_version,
    check_and_prompt_update,
)


def test_parse_version():
    assert _parse_version("1.0.1") == (1, 0, 1)
    assert _parse_version("1.0.2") > _parse_version("1.0.1")
    assert _parse_version("2.0.0") > _parse_version("1.9.9")
    assert _parse_version("1.0.1") == _parse_version("1.0.1")
    assert _parse_version("1.0.0b1") == (1, 0, 0)


def test_pipx_update_prompt_when_newer_version_available():
    mock_console = MagicMock()

    with patch("mailshift.utils.updater._get_local_commit", return_value=None), \
         patch("mailshift.utils.updater._get_installed_version", return_value="1.0.1"), \
         patch("mailshift.utils.updater._get_pypi_version", return_value="1.0.2"):

        check_and_prompt_update(mock_console)

        assert mock_console.print.called
        panel = mock_console.print.call_args[0][0]
        content = str(panel.renderable)
        assert "pipx upgrade mailshift" in content
        assert "1.0.2" in content


def test_pipx_no_prompt_when_up_to_date():
    mock_console = MagicMock()

    with patch("mailshift.utils.updater._get_local_commit", return_value=None), \
         patch("mailshift.utils.updater._get_installed_version", return_value="1.0.1"), \
         patch("mailshift.utils.updater._get_pypi_version", return_value="1.0.1"):

        check_and_prompt_update(mock_console)

        assert not mock_console.print.called
