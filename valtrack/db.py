"""SQLite connection and schema setup for VALTrack."""
import sqlite3
from pathlib import Path

# The database and schema live at the repo root, one level above this package.
REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "valtrack.db"
SCHEMA_PATH = REPO_ROOT / "schema.sql"


def connect(db_path=DB_PATH):
    """Open a connection with foreign keys on and rows accessible by name."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Columns added to existing tables after the first schema was shipped. The
# executescript below creates missing tables, but CREATE TABLE IF NOT EXISTS
# never alters a table that already exists, so new columns on an old table are
# added here instead. Each entry is (table, column, definition).
_ADDED_COLUMNS = [
    ("matches", "map_vetos_raw", "TEXT"),
    ("matches", "details_fetched_at", "TEXT"),
]


def _ensure_columns(conn):
    """Add any columns missing from an existing database. Idempotent."""
    for table, column, definition in _ADDED_COLUMNS:
        existing = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db(db_path=DB_PATH):
    """Create any missing tables from schema.sql. Safe to call repeatedly."""
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    conn = connect(db_path)
    try:
        conn.executescript(schema)
        _ensure_columns(conn)
        conn.commit()
    finally:
        conn.close()


def set_meta(conn, key, value):
    """Upsert a single key/value bookkeeping row."""
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def get_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default
