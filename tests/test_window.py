"""Tests for the date window, the shared filter every derived stat plugs into.

The clause must be valid SQL in all four bound combinations, and contains() must
mirror it so Python-side use and the SQL filter agree on the edges.
"""
from datetime import date

from valtrack.window import (
    DateWindow,
    EventFilter,
    StageFilter,
    classify_stage,
    is_lan_event,
)


def test_all_time_clause_is_always_true():
    w = DateWindow.all_time()
    sql, params = w.clause("date")
    assert sql == "1=1"
    assert params == []
    assert w.is_all_time


def test_start_only_clause():
    w = DateWindow(date(2024, 1, 1), None)
    sql, params = w.clause("date")
    assert sql == "date >= ?"
    assert params == ["2024-01-01"]


def test_end_only_clause_uses_given_column():
    w = DateWindow(None, date(2024, 12, 31))
    sql, params = w.clause("m.date")
    assert sql == "m.date <= ?"
    assert params == ["2024-12-31"]


def test_both_bounds_clause():
    w = DateWindow(date(2024, 1, 1), date(2024, 6, 30))
    sql, params = w.clause()
    assert sql == "date >= ? AND date <= ?"
    assert params == ["2024-01-01", "2024-06-30"]


def test_contains_is_inclusive_on_both_edges():
    w = DateWindow(date(2024, 1, 1), date(2024, 6, 30))
    assert w.contains("2024-01-01")
    assert w.contains("2024-06-30")
    assert w.contains(date(2024, 3, 15))
    assert not w.contains("2023-12-31")
    assert not w.contains("2024-07-01")


def test_contains_none_date_only_in_all_time():
    assert DateWindow.all_time().contains(None)
    assert not DateWindow(date(2024, 1, 1), None).contains(None)


def test_label():
    assert DateWindow.all_time().label == "all time"
    assert DateWindow(date(2024, 1, 1), date(2024, 6, 30)).label == (
        "2024-01-01 to 2024-06-30"
    )


# --- LAN / online event filter ----------------------------------------------

def test_is_lan_event_detects_international_markers():
    assert is_lan_event("Champions Tour 2024: Masters Madrid") is True
    assert is_lan_event("Valorant Champions 2024") is True
    assert is_lan_event("VCT 2025: EMEA Stage 2 Playoffs") is False
    assert is_lan_event("") is False
    assert is_lan_event(None) is False


def test_event_filter_all_is_noop():
    sql, params = EventFilter("all").clause("event_name")
    assert sql == "1=1" and params == []


def test_event_filter_lan_and_online_exclude_unknown_events():
    lan_sql, lan_params = EventFilter("lan").clause("event_name")
    on_sql, on_params = EventFilter("online").clause("event_name")
    # Same bound LAN markers either way, all-qmark so it composes with the window.
    assert lan_params == on_params
    assert lan_params.count("%masters%") == 1
    # Online excludes the LAN markers and also requires a known event name, so a
    # match with no stored event name lands in neither bucket (only "all").
    assert "NOT (" in on_sql
    assert "IS NOT NULL" in on_sql and "IS NOT NULL" not in lan_sql


# --- event-stage classification and filter ----------------------------------

def test_classify_stage_playoff_labels():
    # Clean abbreviations, full words, and an event name glued onto the round.
    for label in ["GF", "UBSF", "UBQF", "LR1", "LBF", "Ro16", "QF", "SF",
                  "Grand Final", "Lower Bracket Final", "Decider (A)",
                  "Elim (B)", "VCT NA S1: MastersGF", "Round of 32", "UBF (B)"]:
        assert classify_stage(label) == "playoff", label


def test_classify_stage_group_labels():
    for label in ["Group A", "Group Stage", "Swiss R3", "Week 1", "W3",
                  "Opening (A)", "Winner's (B)", "Regular Season", "Round Robin"]:
        assert classify_stage(label) == "group", label


def test_classify_stage_unsure_is_unclassified():
    # Genuinely ambiguous tokens are left out rather than guessed into a bucket.
    for label in ["R1", "Day 1", "D2", "Showmatch", "Main Event", "Play-ins",
                  None, "", "Tiebreaker"]:
        assert classify_stage(label) is None, label


def test_classify_stage_playoff_beats_group_when_both_present():
    # A group's bracket final has both markers; the more specific playoff wins.
    assert classify_stage("Group A GF") == "playoff"


def test_classify_stage_uses_event_name_as_backup():
    assert classify_stage(None, "Valorant Champions 2024 Playoffs") == "playoff"
    assert classify_stage(None, "VCT EMEA Stage 1 Group Stage") == "group"


def test_stage_filter_all_is_noop():
    sql, params = StageFilter("all").clause()
    assert sql == "1=1" and params == []


def test_stage_filter_group_and_playoff_are_equality():
    g_sql, g_params = StageFilter("group").clause("match_stage")
    p_sql, p_params = StageFilter("playoff").clause("m.match_stage")
    assert g_sql == "match_stage = ?" and g_params == ["group"]
    assert p_sql == "m.match_stage = ?" and p_params == ["playoff"]
