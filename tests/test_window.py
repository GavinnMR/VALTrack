"""Tests for the date window, the shared filter every derived stat plugs into.

The clause must be valid SQL in all four bound combinations, and contains() must
mirror it so Python-side use and the SQL filter agree on the edges.
"""
from datetime import date

from valtrack.window import DateWindow, EventFilter, is_lan_event


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


# --- LAN / online event filter (Build Step 14) ------------------------------

def test_is_lan_event_detects_international_markers():
    assert is_lan_event("Champions Tour 2024: Masters Madrid") is True
    assert is_lan_event("Valorant Champions 2024") is True
    assert is_lan_event("VCT 2025: EMEA Stage 2 Playoffs") is False
    assert is_lan_event("") is False
    assert is_lan_event(None) is False


def test_event_filter_all_is_noop():
    sql, params = EventFilter("all").clause("event_name")
    assert sql == "1=1" and params == []


def test_event_filter_lan_and_online_are_complementary():
    lan_sql, lan_params = EventFilter("lan").clause("event_name")
    on_sql, on_params = EventFilter("online").clause("event_name")
    # Same bound params, online is the negation of the LAN match.
    assert lan_params == on_params
    assert on_sql == f"NOT {lan_sql}"
    # All-qmark, no named params, so it composes with the date window.
    assert lan_params.count("%masters%") == 1
