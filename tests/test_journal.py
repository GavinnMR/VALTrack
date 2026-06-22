"""Tests for the local notes and matchup log.

These store the user's own input, so the contract is simple but must hold: a note
round-trips and is order independent for a pair, and a log entry can be added,
listed, and later resolved with an outcome.
"""
from valtrack import db, journal


def _conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.ensure_app_tables(conn)
    return conn


def test_note_round_trips_and_is_pair_order_independent(tmp_path):
    conn = _conn(tmp_path)
    assert journal.get_note(conn, 100, 200) == ""  # nothing saved yet
    journal.save_note(conn, 100, 200, "fast attack side, weak on Split")
    assert journal.get_note(conn, 100, 200) == "fast attack side, weak on Split"
    # The same pair selected the other way round reads the same note.
    assert journal.get_note(conn, 200, 100) == "fast attack side, weak on Split"
    # Saving again overwrites rather than duplicating.
    journal.save_note(conn, 200, 100, "updated read")
    assert journal.get_note(conn, 100, 200) == "updated read"
    conn.close()


def test_matchup_log_add_list_and_resolve(tmp_path):
    conn = _conn(tmp_path)
    assert journal.list_log_entries(conn) == []
    journal.add_log_entry(conn, 100, "Alpha", 200, "Beta", "lean Alpha", "high")
    entries = journal.list_log_entries(conn)
    assert len(entries) == 1
    e = entries[0]
    assert e["team_a_name"] == "Alpha" and e["team_b_name"] == "Beta"
    assert e["confidence"] == "high"
    assert e["outcome"] is None and e["created_at"] is not None

    journal.resolve_log_entry(conn, e["id"], "Alpha won 2-1")
    e2 = journal.list_log_entries(conn)[0]
    assert e2["outcome"] == "Alpha won 2-1"
    assert e2["resolved_at"] is not None
    conn.close()


def test_matchup_log_newest_first(tmp_path):
    conn = _conn(tmp_path)
    journal.add_log_entry(conn, 1, "A", 2, "B", "first", "low")
    journal.add_log_entry(conn, 3, "C", 4, "D", "second", "medium")
    notes = [e["note"] for e in journal.list_log_entries(conn)]
    # Newest first, so the second entry leads.
    assert notes == ["second", "first"]
    conn.close()
