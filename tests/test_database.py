import pytest
from pathlib import Path
from unittest.mock import patch

from mailshift.models.models import MailMeta
from mailshift.db import database


@pytest.fixture
def mock_db_file(tmp_path):
    """Patch DB_FILE in database module to use a temporary file for tests."""
    db_path = tmp_path / "test_mailshift.db"
    with patch("mailshift.db.database.DB_FILE", db_path):
        yield db_path


def test_init_db(mock_db_file):
    database.init_db()
    assert mock_db_file.exists()


def test_mails_cache_operations(mock_db_file):
    # Empty cache returns None
    assert database.load_mails_cache() is None

    # Save a couple of mails
    mails = [
        MailMeta(uid="1", subject="Subj 1", sender="Sender 1", size_bytes=100, has_attachment=True),
        MailMeta(uid="2", subject="Subj 2", sender="Sender 2", size_bytes=200, has_attachment=False),
    ]
    database.save_mails_cache(mails)

    # Load from cache
    cached = database.load_mails_cache()
    assert cached is not None
    assert len(cached) == 2

    m1 = next(m for m in cached if m.uid == "1")
    assert m1.subject == "Subj 1"
    assert m1.sender == "Sender 1"
    assert m1.size_bytes == 100
    assert m1.has_attachment is True

    m2 = next(m for m in cached if m.uid == "2")
    assert m2.subject == "Subj 2"
    assert m2.sender == "Sender 2"
    assert m2.size_bytes == 200
    assert m2.has_attachment is False

    # Clear cache
    database.clear_mails_cache()
    assert not mock_db_file.exists()


def test_checkpoint_operations(mock_db_file):
    scope = ("user@example.com", "imap.example.com", "INBOX")
    # Empty checkpoint
    assert database.get_fetched_uids(*scope) == set()

    # Mark some as fetched
    database.mark_uids_fetched(["uid1", "uid2"], *scope)
    fetched = database.get_fetched_uids(*scope)
    assert fetched == {"uid1", "uid2"}

    # Add more
    database.mark_uids_fetched(["uid3"], *scope)
    assert database.get_fetched_uids(*scope) == {"uid1", "uid2", "uid3"}

    # Clear checkpoint
    database.clear_checkpoint(*scope)
    
    # Wait, clear_checkpoint only empties the table, the file still exists
    # but the set should be empty.
    assert database.get_fetched_uids(*scope) == set()


def test_checkpoint_isolated_by_account_host_and_mailbox(mock_db_file):
    scopes = [
        ("one@example.com", "imap.example.com", "INBOX"),
        ("two@example.com", "imap.example.com", "INBOX"),
        ("one@example.com", "other.example.com", "INBOX"),
        ("one@example.com", "imap.example.com", "Archive"),
    ]
    for scope in scopes:
        database.mark_uids_fetched(["same-uid"], *scope)

    assert all(database.get_fetched_uids(*scope) == {"same-uid"} for scope in scopes)
    database.clear_checkpoint(*scopes[0])
    assert database.get_fetched_uids(*scopes[0]) == set()
    assert all(database.get_fetched_uids(*scope) == {"same-uid"} for scope in scopes[1:])

    with database.get_db_connection() as conn:
        scope_keys = [row[0] for row in conn.execute("SELECT DISTINCT scope_key FROM fetch_checkpoint")]
    assert "one@example.com" not in scope_keys


def test_init_db_migrates_legacy_checkpoint_without_losing_rows(mock_db_file):
    import sqlite3

    with sqlite3.connect(mock_db_file) as conn:
        conn.execute("CREATE TABLE fetch_checkpoint (uid TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO fetch_checkpoint (uid) VALUES ('legacy-uid')")

    database._DB_INITIALIZED = False
    database.init_db()

    with database.get_db_connection() as conn:
        assert conn.execute("SELECT scope_key, uid FROM fetch_checkpoint").fetchall() == [
            ("legacy", "legacy-uid")
        ]


def test_get_db_connection_pragmas(mock_db_file):
    with database.get_db_connection() as conn:
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        synchronous = conn.execute("PRAGMA synchronous;").fetchone()[0]
        temp_store = conn.execute("PRAGMA temp_store;").fetchone()[0]

        # PRAGMA journal_mode returns 'wal'
        assert journal_mode == "wal"
        # PRAGMA synchronous NORMAL is 1
        assert synchronous == 1
        # PRAGMA temp_store MEMORY is 2
        assert temp_store == 2


def test_get_db_connection_commit(mock_db_file):
    database.init_db()  # Create tables
    with database.get_db_connection() as conn:
        conn.execute("INSERT INTO fetch_checkpoint (scope_key, uid) VALUES ('test', 'test_uid')")

    # Verify commit happened automatically
    with database.get_db_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM fetch_checkpoint WHERE uid='test_uid'").fetchone()[0]
        assert count == 1


def test_get_db_connection_rollback(mock_db_file):
    database.init_db()  # Create tables
    class DummyException(Exception):
        pass

    with pytest.raises(DummyException):
        with database.get_db_connection() as conn:
            conn.execute("INSERT INTO fetch_checkpoint (scope_key, uid) VALUES ('test', 'rollback_uid')")
            raise DummyException("Force rollback")

    # Verify rollback happened automatically
    with database.get_db_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM fetch_checkpoint WHERE uid='rollback_uid'").fetchone()[0]
        assert count == 0


def test_get_db_connection_closes(mock_db_file):
    with database.get_db_connection() as conn:
        pass  # Just enter and exit

    # The connection should be closed outside the context
    import sqlite3
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")
