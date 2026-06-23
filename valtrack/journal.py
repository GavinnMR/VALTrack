"""Local-only reasoning aids: matchup notes and the matchup log (Build Step 15).

These hold the user's own observations, never anything scraped. A note is a free
text field per team pair; the matchup log records a matchup with a pre-match note
and a confidence level, then later the actual outcome, so past calls can be
reviewed. Everything is stored in the local SQLite database and stays on the
machine.

The tables are created by db.ensure_app_tables, which the app calls on startup,
so these functions assume they exist.
"""
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pair_key(team_a_id, team_b_id):
    """A stable key for a team pair, order independent.

    The same two teams map to one note whichever is picked as A or B, so the key
    is the two ids sorted and joined.
    """
    low, high = sorted((int(team_a_id), int(team_b_id)))
    return f"{low}-{high}"


def get_note(conn, team_a_id, team_b_id):
    """The saved note for a team pair, or an empty string when there is none."""
    row = conn.execute(
        "SELECT body FROM matchup_notes WHERE pair_key = ?",
        (pair_key(team_a_id, team_b_id),),
    ).fetchone()
    return row["body"] if row and row["body"] else ""


def save_note(conn, team_a_id, team_b_id, body):
    """Upsert the note for a team pair."""
    conn.execute(
        """
        INSERT INTO matchup_notes (pair_key, body, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(pair_key) DO UPDATE SET
            body = excluded.body,
            updated_at = excluded.updated_at
        """,
        (pair_key(team_a_id, team_b_id), body, _now()),
    )
    conn.commit()


def add_log_entry(conn, team_a_id, team_a_name, team_b_id, team_b_name,
                  note, confidence):
    """Record a new matchup log entry, outcome left open for later."""
    conn.execute(
        """
        INSERT INTO matchup_log (
            team_a_id, team_a_name, team_b_id, team_b_name,
            note, confidence, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (team_a_id, team_a_name, team_b_id, team_b_name, note, confidence, _now()),
    )
    conn.commit()


def list_log_entries(conn):
    """All matchup log entries, newest first."""
    return conn.execute(
        "SELECT * FROM matchup_log ORDER BY created_at DESC, id DESC"
    ).fetchall()


def resolve_log_entry(conn, entry_id, outcome, outcome_side=None):
    """Record the actual outcome for a log entry and stamp when it was resolved.

    `outcome` is the human-readable result (for example a score). `outcome_side`
    is the structured winner, "a" or "b" (or None when left open), so past calls
    can be reviewed as cleanly resolved rather than parsed back out of free text.
    """
    conn.execute(
        "UPDATE matchup_log SET outcome = ?, outcome_side = ?, resolved_at = ? "
        "WHERE id = ?",
        (outcome, outcome_side, _now(), entry_id),
    )
    conn.commit()


def update_log_entry(conn, entry_id, note, confidence):
    """Edit the pre-match note and confidence of an existing log entry.

    Fixing a typo in a note or a misjudged confidence should not mean deleting and
    re-adding the call, so this updates them in place. The outcome and timestamps
    are left untouched.
    """
    conn.execute(
        "UPDATE matchup_log SET note = ?, confidence = ? WHERE id = ?",
        (note, confidence, entry_id),
    )
    conn.commit()


def delete_log_entry(conn, entry_id):
    """Remove a log entry entirely, for a mistaken or unwanted call."""
    conn.execute("DELETE FROM matchup_log WHERE id = ?", (entry_id,))
    conn.commit()
