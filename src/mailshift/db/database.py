import sqlite3
from pathlib import Path
from typing import Optional, Iterator, List, Set, Dict
from contextlib import contextmanager

from ..models.models import MailMeta
from ..utils.paths import get_path

DB_FILE = get_path("mailshift.db")
_DB_INITIALIZED = False

@contextmanager
def get_db_connection() -> Iterator[sqlite3.Connection]:
    """Provide a transactional scope around a series of operations."""
    # Timeout 15.0 saniye yapıldı: Çoklu thread okuma/yazmalarında "database is locked" engellenir
    conn = sqlite3.connect(DB_FILE, timeout=15.0)
    try:
        # Performans: WAL (Write-Ahead Logging) ve Memory tabanlı geçici depolama
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA temp_store=MEMORY;")
        
        with conn:  # Hata olmazsa otomatik commit, hata olursa rollback yapar
            yield conn
    finally:
        conn.close()

def init_db() -> None:
    """Initialize the SQLite database with required tables."""
    global _DB_INITIALIZED
    if _DB_INITIALIZED and DB_FILE.exists():
        return

    with get_db_connection() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS mails_cache (
                uid TEXT PRIMARY KEY,
                subject TEXT,
                sender TEXT,
                date TEXT,
                size_bytes INTEGER,
                body_preview TEXT,
                has_attachment INTEGER
            );
            
            -- Checkpoint table: tracks which UIDs have been fully processed
            CREATE TABLE IF NOT EXISTS fetch_checkpoint (
                uid TEXT PRIMARY KEY
            );
        ''')
    _DB_INITIALIZED = True

# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def save_mails_cache(mails: List[MailMeta], batch_size: int = 500) -> None:
    """Persist a list of MailMeta objects to the SQLite cache."""
    if not mails:
        return
        
    init_db()
    rows = [
        (m.uid, m.subject, m.sender, m.date, m.size_bytes, m.body_preview, 1 if m.has_attachment else 0)
        for m in mails
    ]
    
    with get_db_connection() as conn:
        for i in range(0, len(rows), batch_size):
            conn.executemany(
                '''
                INSERT OR REPLACE INTO mails_cache
                (uid, subject, sender, date, size_bytes, body_preview, has_attachment)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ''',
                rows[i : i + batch_size],
            )

def load_mails_cache() -> Optional[List[MailMeta]]:
    """Load cached MailMeta objects from SQLite."""
    if not DB_FILE.exists():
        return None
        
    init_db()
    try:
        with get_db_connection() as conn:
            cursor = conn.execute('''
                SELECT uid, subject, sender, date, size_bytes, body_preview, has_attachment 
                FROM mails_cache
            ''')
            rows = cursor.fetchall()
            
            if not rows:
                return None
                
            return [
                MailMeta(
                    uid=r[0], subject=r[1], sender=r[2], date=r[3],
                    size_bytes=r[4], body_preview=r[5], has_attachment=bool(r[6])
                ) for r in rows
            ]
    except sqlite3.Error:
        return None

def load_mails_cache_by_uids(uids: List[str], batch_size: int = 500) -> List[MailMeta]:
    """Load only cached MailMeta rows for the provided UID list."""
    if not uids or not DB_FILE.exists():
        return []
        
    init_db()
    rows_by_uid: Dict[str, MailMeta] = {}
    
    try:
        with get_db_connection() as conn:
            # UIDs chunking to respect SQLite variable limits (usually 999)
            for i in range(0, len(uids), batch_size):
                chunk = uids[i : i + batch_size]
                placeholders = ",".join("?" for _ in chunk)
                
                cursor = conn.execute(
                    f"""
                    SELECT uid, subject, sender, date, size_bytes, body_preview, has_attachment
                    FROM mails_cache
                    WHERE uid IN ({placeholders})
                    """,
                    chunk,
                )
                
                for r in cursor.fetchall():
                    rows_by_uid[r[0]] = MailMeta(
                        uid=r[0], subject=r[1], sender=r[2], date=r[3],
                        size_bytes=r[4], body_preview=r[5], has_attachment=bool(r[6])
                    )
    except sqlite3.Error:
        return []

    # Orijinal UID sırasını koruyarak döndür
    return [rows_by_uid[uid] for uid in uids if uid in rows_by_uid]

def clear_mails_cache() -> None:
    """Clear cached mail data by removing the SQLite file for a fully fresh scan."""
    global _DB_INITIALIZED
    if not DB_FILE.exists():
        return

    try:
        DB_FILE.unlink()
        _DB_INITIALIZED = False
        return
    except OSError:
        # Fallback for transient file locks: best-effort cache cleanup.
        init_db()
        with get_db_connection() as conn:
            conn.execute("DELETE FROM mails_cache")

# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------

def mark_uids_fetched(uids: List[str]) -> None:
    """Record that *uids* have been successfully fetched in the current run."""
    if not uids:
        return
        
    init_db()
    with get_db_connection() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO fetch_checkpoint (uid) VALUES (?)",
            [(u,) for u in uids],
        )

def get_fetched_uids() -> Set[str]:
    """Return the set of UIDs already checkpointed (fetched) in this run."""
    if not DB_FILE.exists():
        return set()
        
    init_db()
    try:
        with get_db_connection() as conn:
            cursor = conn.execute("SELECT uid FROM fetch_checkpoint")
            return {row[0] for row in cursor.fetchall()}
    except sqlite3.Error:
        return set()

def clear_checkpoint() -> None:
    """Wipe the checkpoint table so the next run starts fresh."""
    if not DB_FILE.exists():
        return
        
    init_db()
    with get_db_connection() as conn:
        conn.execute("DELETE FROM fetch_checkpoint")
