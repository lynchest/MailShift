from unittest.mock import MagicMock, patch

import ssl

from config import AppConfig, Mode, Provider, build_imap_config
from engine import MailEngine


def _make_app_cfg() -> AppConfig:
    imap = build_imap_config(Provider.GMAIL, "u@g.com", "p")
    return AppConfig(provider=Provider.GMAIL, mode=Mode.FAST, imap=imap)


def test_delete_mails_reconnects_after_ssl_eof() -> None:
    cfg = _make_app_cfg()
    engine = MailEngine(cfg)

    original_conn = MagicMock()
    original_conn.uid.side_effect = ssl.SSLEOFError("EOF occurred in violation of protocol")
    original_conn.logout.return_value = "BYE"

    reconnected_conn = MagicMock()
    reconnected_conn.uid.return_value = ("OK", [b"done"])
    reconnected_conn.expunge.return_value = ("OK", [b"expunged"])

    engine._conn = original_conn

    with patch("engine._connect", return_value=reconnected_conn) as mock_connect:
        deleted = engine.delete_mails(["1", "2"])

    assert deleted == ["1", "2"]
    assert mock_connect.call_count >= 1
    reconnected_conn.select.assert_called_with("INBOX")


def test_delete_mails_returns_empty_when_expunge_fails_after_retries() -> None:
    cfg = _make_app_cfg()
    engine = MailEngine(cfg)

    conn = MagicMock()
    conn.uid.return_value = ("OK", [b"done"])
    conn.expunge.side_effect = ssl.SSLEOFError("EOF occurred in violation of protocol")

    reconnect_conn = MagicMock()
    reconnect_conn.expunge.side_effect = ssl.SSLEOFError("EOF occurred in violation of protocol")

    engine._conn = conn

    with patch("engine._connect", return_value=reconnect_conn) as mock_connect:
        deleted = engine.delete_mails(["1"])

    assert deleted == []
    assert mock_connect.call_count >= 1
