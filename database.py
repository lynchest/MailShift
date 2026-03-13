import sqlite3
from pathlib import Path
from typing import Optional

from models import MailMeta

DB_FILE = Path("mailshift.db")

def init_db() -> None:
    """Initialize the SQLite database with required tables."""
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS mails_cache (
                uid TEXT PRIMARY KEY,
                subject TEXT,
                sender TEXT,
                date TEXT,
                size_bytes INTEGER,
                body_preview TEXT,
                has_attachment INTEGER
            )
        ''')
        # Checkpoint table: tracks which UIDs have been fully processed in the
        # current scan session so a crashed/interrupted run can resume from
        # where it left off instead of starting over.
        conn.execute('''
            CREATE TABLE IF NOT EXISTS fetch_checkpoint (
                uid TEXT PRIMARY KEY
            )
        ''')

# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def save_mails_cache(mails: list[MailMeta], batch_size: int = 500) -> None:
    """Persist a list of MailMeta objects to the SQLite cache.

    Rows are committed in batches of *batch_size* so that very large lists
    do not cause a single enormous transaction that holds the write-lock for
    too long.
    """
    if not mails:
        return
    init_db()
    rows = [
        (m.uid, m.subject, m.sender, m.date, m.size_bytes, m.body_preview, 1 if m.has_attachment else 0)
        for m in mails
    ]
    with sqlite3.connect(DB_FILE) as conn:
        for i in range(0, len(rows), batch_size):
            conn.executemany(
                '''
                INSERT OR REPLACE INTO mails_cache
                (uid, subject, sender, date, size_bytes, body_preview, has_attachment)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ''',
                rows[i : i + batch_size],
            )
            conn.commit()

def load_mails_cache() -> Optional[list[MailMeta]]:
    """Load cached MailMeta objects from SQLite."""
    if not DB_FILE.exists():
        return None
    init_db()
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.execute('''
                SELECT uid, subject, sender, date, size_bytes, body_preview, has_attachment 
                FROM mails_cache
            ''')
            rows = cursor.fetchall()
            if not rows:
                return None
            return [
                MailMeta(
                    uid=r[0],
                    subject=r[1],
                    sender=r[2],
                    date=r[3],
                    size_bytes=r[4],
                    body_preview=r[5],
                    has_attachment=bool(r[6])
                ) for r in rows
            ]
    except Exception:
        return None

def clear_mails_cache() -> None:
    """Remove the SQLite database file."""
    DB_FILE.unlink(missing_ok=True)

# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------

def mark_uids_fetched(uids: list[str]) -> None:
    """Record that *uids* have been successfully fetched in the current run."""
    if not uids:
        return
    init_db()
    with sqlite3.connect(DB_FILE) as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO fetch_checkpoint (uid) VALUES (?)",
            [(u,) for u in uids],
        )

def get_fetched_uids() -> set[str]:
    """Return the set of UIDs already checkpointed (fetched) in this run."""
    if not DB_FILE.exists():
        return set()
    init_db()
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.execute("SELECT uid FROM fetch_checkpoint")
            return {row[0] for row in cursor.fetchall()}
    except Exception:
        return set()

def clear_checkpoint() -> None:
    """Wipe the checkpoint table so the next run starts fresh."""
    if not DB_FILE.exists():
        return
    init_db()
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("DELETE FROM fetch_checkpoint")
