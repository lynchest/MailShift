from unittest.mock import MagicMock

from config import AppConfig, Mode, Provider, build_imap_config
from engine import MailEngine


def _make_gmail_engine() -> MailEngine:
    imap = build_imap_config(Provider.GMAIL, "u@gmail.com", "p")
    cfg = AppConfig(provider=Provider.GMAIL, mode=Mode.FAST, imap=imap)
    return MailEngine(cfg)


def test_google_delete_mails_sets_deleted_and_expunge() -> None:
    engine = _make_gmail_engine()

    conn = MagicMock()
    conn.uid.return_value = ("OK", [b"done"])
    conn.expunge.return_value = ("OK", [b"expunged"])
    engine._conn = conn

    progress: list[str] = []
    deleted = engine.delete_mails(["101", "102"], progress_cb=progress.append)

    assert deleted == ["101", "102"]
    assert progress == ["101", "102"]
    conn.uid.assert_called_once_with("store", "101,102", "+FLAGS", r"(\Deleted)")
    conn.expunge.assert_called_once()


def test_google_move_to_trash_uses_gmail_trash_folder_and_marks_deleted() -> None:
    engine = _make_gmail_engine()

    conn = MagicMock()
    conn.list.return_value = (
        "OK",
        [b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Trash"'],
    )
    conn.uid.side_effect = [
        ("OK", [b"copied"]),
        ("OK", [b"flagged"]),
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
    assert conn.uid.call_count == 2
    conn.uid.assert_any_call("copy", "501", '"[Gmail]/Trash"')
    conn.uid.assert_any_call("store", "501", "+FLAGS", r"(\Deleted)")
    conn.expunge.assert_called_once()
