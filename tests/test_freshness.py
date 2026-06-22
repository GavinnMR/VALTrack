"""Tests for the data-age helper behind the staleness banner.

The banner's stale state hinges on this age, so it must read a stored timestamp
correctly, treat a naive timestamp as UTC, and report unknown freshness as None
rather than as fresh.
"""
from datetime import datetime, timezone

from valtrack.freshness import age_days

NOW = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)


def test_age_days_from_aware_timestamp():
    age = age_days("2026-06-15T12:00:00+00:00", now=NOW)
    assert age == 7.0


def test_age_days_treats_naive_as_utc():
    age = age_days("2026-06-21T12:00:00", now=NOW)
    assert age == 1.0


def test_age_days_missing_or_bad_is_none():
    assert age_days(None, now=NOW) is None
    assert age_days("", now=NOW) is None
    assert age_days("not a date", now=NOW) is None
