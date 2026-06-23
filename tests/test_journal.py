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


def test_matchup_log_structured_outcome(tmp_path):
    conn = _conn(tmp_path)
    journal.add_log_entry(conn, 100, "Alpha", 200, "Beta", "lean Alpha", "high")
    e = journal.list_log_entries(conn)[0]
    journal.resolve_log_entry(conn, e["id"], "Alpha won 2-1", outcome_side="a")
    e2 = journal.list_log_entries(conn)[0]
    assert e2["outcome"] == "Alpha won 2-1"
    assert e2["outcome_side"] == "a"
    assert e2["resolved_at"] is not None
    conn.close()


def test_matchup_log_edit_note_and_confidence(tmp_path):
    conn = _conn(tmp_path)
    journal.add_log_entry(conn, 1, "A", 2, "B", "typpo", "low")
    e = journal.list_log_entries(conn)[0]
    journal.update_log_entry(conn, e["id"], "fixed note", "high")
    e2 = journal.list_log_entries(conn)[0]
    assert e2["note"] == "fixed note" and e2["confidence"] == "high"
    # Editing does not resolve the entry.
    assert e2["outcome"] is None
    conn.close()


def test_matchup_log_delete(tmp_path):
    conn = _conn(tmp_path)
    journal.add_log_entry(conn, 1, "A", 2, "B", "first", "low")
    journal.add_log_entry(conn, 3, "C", 4, "D", "second", "medium")
    entries = journal.list_log_entries(conn)
    journal.delete_log_entry(conn, entries[0]["id"])  # remove the newest
    remaining = journal.list_log_entries(conn)
    assert [e["note"] for e in remaining] == ["first"]
    conn.close()


def test_favorites_add_list_and_remove_order_independent(tmp_path):
    conn = _conn(tmp_path)
    assert journal.is_favorite(conn, 100, 200) is False
    journal.add_favorite(conn, 100, "Alpha", 200, "Beta")
    # The same pair the other way round is the same favorite (order independent).
    assert journal.is_favorite(conn, 200, 100) is True
    journal.add_favorite(conn, 200, "Beta", 100, "Alpha")  # idempotent
    assert len(journal.list_favorites(conn)) == 1
    journal.remove_favorite(conn, 100, 200)
    assert journal.is_favorite(conn, 100, 200) is False
    conn.close()


def test_upcoming_tag_round_trips_and_clears(tmp_path):
    conn = _conn(tmp_path)
    assert journal.get_upcoming(conn, 1, 2) is None
    journal.save_upcoming(conn, 1, 2, "2026-07-01", "Masters Toronto", True)
    up = journal.get_upcoming(conn, 2, 1)  # order independent
    assert up["match_date"] == "2026-07-01"
    assert up["event_name"] == "Masters Toronto" and up["is_lan"] is True
    # Upsert overwrites rather than duplicating.
    journal.save_upcoming(conn, 1, 2, "2026-07-02", "Regional", False)
    assert journal.get_upcoming(conn, 1, 2)["is_lan"] is False
    journal.clear_upcoming(conn, 1, 2)
    assert journal.get_upcoming(conn, 1, 2) is None
    conn.close()
