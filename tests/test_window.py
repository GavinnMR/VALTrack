"""Tests for the date window, the shared filter every derived stat plugs into.

The clause must be valid SQL in all four bound combinations, and contains() must
mirror it so Python-side use and the SQL filter agree on the edges.
"""
from datetime import date

from valtrack.window import DateWindow


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
