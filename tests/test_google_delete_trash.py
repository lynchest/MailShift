from unittest.mock import MagicMock

from mailshift.config.config import AppConfig, Mode, Provider, build_imap_config
from mailshift.core.engine import MailEngine


def _make_gmail_engine() -> MailEngine:
    imap = build_imap_config(Provider.GMAIL, "u@gmail.com", "p")
    cfg = AppConfig(provider=Provider.GMAIL, mode=Mode.FAST, imap=imap)
    return MailEngine(cfg)


def test_google_delete_mails_sets_deleted_and_expunge() -> None:
    engine = _make_gmail_engine()

    conn = MagicMock()
    conn.capabilities = (b"IMAP4REV1", b"UIDPLUS")
    conn.uid.return_value = ("OK", [b"done"])
    conn.expunge.return_value = ("OK", [b"expunged"])
    engine._conn = conn

    progress: list[str] = []
    deleted = engine.delete_mails(["101", "102"], progress_cb=progress.append)

    assert deleted == ["101", "102"]
    assert progress == ["101", "102"]
    conn.uid.assert_any_call("store", "101,102", "+FLAGS", r"(\Deleted)")
    conn.uid.assert_any_call("expunge", "101,102")


def test_google_move_to_trash_uses_gmail_trash_folder_and_marks_deleted() -> None:
    engine = _make_gmail_engine()

    conn = MagicMock()
    conn.capabilities = (b"IMAP4REV1", b"UIDPLUS")
    conn.list.return_value = (
        "OK",
        [b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Trash"'],
    )
    conn.uid.side_effect = [
        ("OK", [b"copied"]),
        ("OK", [b"flagged"]),
        ("OK", [b"expunged"]),
    ]
    conn.expunge.return_value = ("OK", [b"expunged"])
    engine._conn = conn

    progress: list[str] = []
    moved = engine.move_to_trash(
        ["501"],
        trash_folder="[Gmail]/Trash",
        progress_cb=progress.append,
    )

    assert moved == ["501"]
    assert progress == ["501"]
    assert conn.uid.call_count == 3
    conn.uid.assert_any_call("copy", "501", '"[Gmail]/Trash"')
    conn.uid.assert_any_call("store", "501", "+FLAGS", r"(\Deleted)")
    conn.uid.assert_any_call("expunge", "501")


def test_google_move_to_trash_falls_back_when_hint_copy_returns_no() -> None:
    engine = _make_gmail_engine()

    conn = MagicMock()
    conn.capabilities = (b"IMAP4REV1", b"UIDPLUS")
    conn.list.return_value = ("NO", [b"failed"])
    conn.uid.side_effect = [
        ("NO", [b"bad destination"]),
        ("OK", [b"copied"]),
        ("OK", [b"flagged"]),
        ("OK", [b"expunged"]),
    ]
    conn.expunge.return_value = ("OK", [b"expunged"])
    engine._conn = conn

    moved = engine.move_to_trash(["777"], trash_folder="Trash")

    assert moved == ["777"]
    conn.uid.assert_any_call("copy", "777", '"Trash"')
    conn.uid.assert_any_call("copy", "777", '"[Gmail]/Trash"')
    conn.uid.assert_any_call("store", "777", "+FLAGS", r"(\Deleted)")
    conn.uid.assert_any_call("expunge", "777")


def test_google_move_to_trash_refreshes_candidates_after_initial_list_failure() -> None:
    engine = _make_gmail_engine()

    conn = MagicMock()
    conn.capabilities = (b"IMAP4REV1", b"UIDPLUS")
    conn.list.side_effect = [
        ("NO", [b"failed"]),
        ("OK", [b'(\\HasNoChildren \\Trash) "/" "[Gmail]/&AMc-w7Y-p Kutusu"']),
    ]

    def _uid_side_effect(command: str, _uid: str, *args):
        if command == "copy":
            folder = args[0]
            if folder == '"[Gmail]/&AMc-w7Y-p Kutusu"':
                return ("OK", [b"copied"])
            return ("NO", [b"bad destination"])
        if command == "store":
            return ("OK", [b"flagged"])
        if command == "expunge":
            return ("OK", [b"expunged"])
        raise AssertionError(f"Unexpected UID command: {command}")

    conn.uid.side_effect = _uid_side_effect
    conn.expunge.return_value = ("OK", [b"expunged"])
    engine._conn = conn

    moved = engine.move_to_trash(["900"], trash_folder="[Gmail]/Trash")

    assert moved == ["900"]
    assert conn.list.call_count >= 2
    conn.uid.assert_any_call("copy", "900", '"[Gmail]/Trash"')
    conn.uid.assert_any_call("copy", "900", '"[Gmail]/&AMc-w7Y-p Kutusu"')
    conn.uid.assert_any_call("store", "900", "+FLAGS", r"(\Deleted)")
    conn.uid.assert_any_call("expunge", "900")


def test_delete_without_uidplus_cancels_when_deleted_messages_already_exist() -> None:
    engine = _make_gmail_engine()
    conn = MagicMock()
    conn.capabilities = (b"IMAP4REV1",)
    conn.uid.return_value = ("OK", [b"8 9"])
    engine._conn = conn

    assert engine.delete_mails(["101"]) == []
    conn.uid.assert_called_once_with("search", None, "DELETED")
    conn.expunge.assert_not_called()


def test_trash_copy_connection_loss_does_not_retry_uncertain_copy() -> None:
    import ssl

    engine = _make_gmail_engine()
    conn = MagicMock()
    conn.capabilities = (b"IMAP4REV1", b"UIDPLUS")
    conn.list.return_value = ("OK", [b'(\\HasNoChildren \\Trash) "/" "Trash"'])
    conn.uid.side_effect = ssl.SSLEOFError("EOF occurred in violation of protocol")
    replacement = MagicMock()
    replacement.capabilities = (b"IMAP4REV1", b"UIDPLUS")
    replacement.select.return_value = ("OK", [b"1"])
    engine._conn = conn

    from unittest.mock import patch
    with patch("mailshift.core.engine._connect", return_value=replacement):
        assert engine.move_to_trash(["301"], trash_folder="Trash") == []

    assert conn.uid.call_count == 1
    replacement.uid.assert_not_called()
