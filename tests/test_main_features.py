import click
import pytest
from unittest.mock import patch

from mailshift.config.config import AppConfig, Mode, Provider, build_imap_config
from mailshift.main import ensure_proton_bridge_ready, format_imap_date, parse_cli_date


def _make_cfg(provider: Provider) -> AppConfig:
    username = "user@proton.me" if provider == Provider.PROTON else "u@gmail.com"
    imap = build_imap_config(provider, username, "secret")
    return AppConfig(provider=provider, mode=Mode.FAST, imap=imap)


def test_parse_cli_date_accepts_iso_and_formats_for_imap() -> None:
    parsed = parse_cli_date("2025-01-01", "--since")

    assert parsed is not None
    assert format_imap_date(parsed) == "01-Jan-2025"


def test_parse_cli_date_accepts_imap_style() -> None:
    parsed = parse_cli_date("15-feb-2025", "--before")

    assert parsed is not None
    assert format_imap_date(parsed) == "15-Feb-2025"


def test_parse_cli_date_rejects_invalid_input() -> None:
    with pytest.raises(click.BadParameter):
        parse_cli_date("2025-13-99", "--since")


def test_ensure_proton_bridge_ready_skips_for_non_proton() -> None:
    cfg = _make_cfg(Provider.GMAIL)

    with patch("mailshift.main._can_open_tcp") as probe:
        assert ensure_proton_bridge_ready(cfg) is True
        probe.assert_not_called()


def test_ensure_proton_bridge_ready_retries_until_ready() -> None:
    cfg = _make_cfg(Provider.PROTON)

    with patch("mailshift.main._can_open_tcp", side_effect=[False, False, True]) as probe, \
         patch("mailshift.main.Prompt.ask", return_value="") as prompt, \
         patch("mailshift.main.console.print"):
        assert ensure_proton_bridge_ready(cfg, max_checks=3) is True

    assert probe.call_count == 3
    assert prompt.call_count == 2


def test_ensure_proton_bridge_ready_allows_quit() -> None:
    cfg = _make_cfg(Provider.PROTON)

    with patch("mailshift.main._can_open_tcp", return_value=False), \
         patch("mailshift.main.Prompt.ask", return_value="q"), \
         patch("mailshift.main.console.print"):
        assert ensure_proton_bridge_ready(cfg, max_checks=3) is False
