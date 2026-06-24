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
    ("matches", "match_format", "TEXT"),
    ("matches", "match_stage", "TEXT"),
    ("map_player_stats", "first_kills_atk", "INTEGER"),
    ("map_player_stats", "first_kills_def", "INTEGER"),
    ("map_player_stats", "first_deaths_atk", "INTEGER"),
    ("map_player_stats", "first_deaths_def", "INTEGER"),
    ("map_player_stats", "clutch_won", "INTEGER"),
    ("map_player_stats", "clutch_lost", "INTEGER"),
    ("map_results", "picked_by_name", "TEXT"),
    ("matchup_log", "outcome_side", "TEXT"),
    ("matchup_log", "predicted_side", "TEXT"),
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


def ensure_columns(conn):
    """Run the idempotent column migration on an existing connection.

    The app opens a database that may predate later columns (the harvest adds
    them through init_db, but the app does not run the full schema). Calling this
    on startup self-heals an older database so a missing column does not crash a
    view. Safe to call every run.
    """
    _ensure_columns(conn)
    conn.commit()


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


# The local-only tables that hold user input (notes and the matchup log). The
# app writes these itself, so they must exist even on a database harvested before
# they were added. The app does not run the full schema, so it ensures just these
# on startup. Kept in sync with schema.sql.
_APP_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS matchup_notes (
    pair_key   TEXT PRIMARY KEY,
    body       TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS matchup_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    team_a_id    INTEGER,
    team_a_name  TEXT,
    team_b_id    INTEGER,
    team_b_name  TEXT,
    note         TEXT,
    confidence   TEXT,
    predicted_side TEXT,
    outcome      TEXT,
    outcome_side TEXT,
    created_at   TEXT,
    resolved_at  TEXT
);
CREATE TABLE IF NOT EXISTS matchup_favorites (
    pair_key    TEXT PRIMARY KEY,
    team_a_id   INTEGER,
    team_a_name TEXT,
    team_b_id   INTEGER,
    team_b_name TEXT,
    created_at  TEXT
);
CREATE TABLE IF NOT EXISTS matchup_upcoming (
    pair_key    TEXT PRIMARY KEY,
    match_date  TEXT,
    event_name  TEXT,
    is_lan      INTEGER,
    updated_at  TEXT
);
CREATE TABLE IF NOT EXISTS app_prefs (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def ensure_app_tables(conn):
    """Create the local notes and matchup-log tables if missing. Idempotent."""
    conn.executescript(_APP_TABLES_SQL)
    conn.commit()


# Harvest-populated analytics tables added after the first schema shipped. The
# harvest creates them through init_db, but the app does not run the full schema,
# so it ensures these on startup too, the same way it ensures the app tables. Kept
# in sync with schema.sql.
_ANALYTICS_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS match_player_perf (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id     INTEGER NOT NULL,
    player_id    INTEGER,
    player_name  TEXT,
    team_name    TEXT,
    mk_2k        INTEGER,
    mk_3k        INTEGER,
    mk_4k        INTEGER,
    mk_5k        INTEGER,
    clutch_1v1   INTEGER,
    clutch_1v2   INTEGER,
    clutch_1v3   INTEGER,
    clutch_1v4   INTEGER,
    clutch_1v5   INTEGER,
    plants       INTEGER,
    defuses      INTEGER,
    fetched_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_match_player_perf_match ON match_player_perf (match_id);
CREATE INDEX IF NOT EXISTS idx_match_player_perf_player ON match_player_perf (player_id);
CREATE TABLE IF NOT EXISTS map_economy (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id     INTEGER NOT NULL,
    map_name     TEXT,
    team_name    TEXT,
    buy_type     TEXT,
    played       INTEGER,
    won          INTEGER,
    fetched_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_map_economy_match ON map_economy (match_id);
"""


def ensure_analytics_tables(conn):
    """Create the per-match performance and economy tables if missing. Idempotent."""
    conn.executescript(_ANALYTICS_TABLES_SQL)
    conn.commit()


def backfill_match_stage(conn):
    """Classify the stage (group/playoff/unknown) of any match missing one.

    The event-stage filter reads matches.match_stage, but the classifier lives in
    Python (the round labels are too varied for a clean SQL rule), so the column
    is filled here. Only rows with a NULL stage are touched, so the first run
    classifies the whole table once and later runs only pick up newly ingested
    matches; an unclassifiable label is stored as "unknown" rather than left NULL
    so it is not rescanned every startup. Safe to call on every app launch.
    """
    from valtrack.window import classify_stage

    rows = conn.execute(
        "SELECT match_id, event_round, event_name FROM matches "
        "WHERE match_stage IS NULL"
    ).fetchall()
    for row in rows:
        stage = classify_stage(row["event_round"], row["event_name"]) or "unknown"
        conn.execute(
            "UPDATE matches SET match_stage = ? WHERE match_id = ?",
            (stage, row["match_id"]),
        )
    if rows:
        conn.commit()


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
