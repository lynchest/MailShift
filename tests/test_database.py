import pytest
from pathlib import Path
from unittest.mock import patch

from models import MailMeta
import database


@pytest.fixture
def mock_db_file(tmp_path):
    """Patch DB_FILE in database module to use a temporary file for tests."""
    db_path = tmp_path / "test_mailshift.db"
    with patch("database.DB_FILE", db_path):
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
    # Empty checkpoint
    assert database.get_fetched_uids() == set()

    # Mark some as fetched
    database.mark_uids_fetched(["uid1", "uid2"])
    fetched = database.get_fetched_uids()
    assert fetched == {"uid1", "uid2"}

    # Add more
    database.mark_uids_fetched(["uid3"])
    assert database.get_fetched_uids() == {"uid1", "uid2", "uid3"}

    # Clear checkpoint
    database.clear_checkpoint()
    
    # Wait, clear_checkpoint only empties the table, the file still exists
    # but the set should be empty.
    assert database.get_fetched_uids() == set()
