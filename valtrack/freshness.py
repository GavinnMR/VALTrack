"""Data-freshness helper for the staleness banner (Build Step 16).

The ingestion engine stamps a last_updated timestamp in the meta table on every
run. This turns that timestamp into an age in days so the app can decide whether
the stored data is stale (the user has not refreshed in a while). Kept separate
and pure so the age calculation can be tested without standing up the app.
"""
from datetime import datetime, timezone


def age_days(last_updated_iso, now=None):
    """Days since the last update, or None when the timestamp is missing or bad.

    `last_updated_iso` is the stored ISO timestamp. `now` defaults to the current
    UTC time and is injectable for tests. A naive timestamp is read as UTC, since
    that is what the ingestion writes. A None or unparseable value returns None,
    so the caller treats unknown freshness as its own case rather than as fresh.
    """
    if not last_updated_iso:
        return None
    try:
        stamp = datetime.fromisoformat(last_updated_iso)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (now - stamp).total_seconds() / 86400.0
