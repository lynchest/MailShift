from unittest.mock import MagicMock

from mailshift.config.config import AppConfig, Mode, Provider, build_imap_config
from mailshift.core.engine import MailEngine


def _make_engine(
    since: str | None = None,
    before: str | None = None,
    scan_limit: int | None = None,
) -> MailEngine:
    imap = build_imap_config(Provider.GMAIL, "u@g.com", "p")
    cfg = AppConfig(
        provider=Provider.GMAIL,
        mode=Mode.FAST,
        imap=imap,
        since=since,
        before=before,
        scan_limit=scan_limit,
    )
    engine = MailEngine(cfg)
    engine._conn = MagicMock()
    return engine


def test_list_uids_uses_since_filter() -> None:
    engine = _make_engine(since="01-Jan-2025")
    engine._conn.uid.return_value = ("OK", [b"30 20 10"])

    uids = engine.list_uids()

    assert uids == ["10", "20", "30"]
    engine._conn.uid.assert_called_once_with(
        "search", None, "ALL", "SINCE", "01-Jan-2025"
    )


def test_list_uids_uses_since_before_and_scan_limit() -> None:
    engine = _make_engine(since="01-Jan-2025", before="01-Feb-2025", scan_limit=2)
    engine._conn.uid.return_value = ("OK", [b"5 4 3 2 1"])

    uids = engine.list_uids()

    assert uids == ["1", "2"]
    engine._conn.uid.assert_called_once_with(
        "search", None, "ALL", "SINCE", "01-Jan-2025", "BEFORE", "01-Feb-2025"
    )


def test_list_uids_with_failed_search_returns_empty() -> None:
    engine = _make_engine(before="01-Jan-2024")
    engine._conn.uid.return_value = ("NO", [b""])

    assert engine.list_uids() == []
