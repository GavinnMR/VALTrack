"""Date windowing for VALTrack.

The comparison runs over a user-chosen date range, all time by default. This is
the one place that knows how a window turns into a SQL filter, so every derived
statistic in later build steps shares it: build a DateWindow once, then AND its
clause into whatever query reads from a dated table.

A window is a pair of inclusive bounds, each optional. All time leaves both
bounds open. The clause is always a valid boolean expression, so callers can
drop it straight into a WHERE without special-casing the all-time case.
"""
from dataclasses import dataclass
from datetime import date

# Event-name fragments that mark an international LAN event. The data has no LAN
# flag, so the environment is inferred from the event name, which is best effort:
# these international events are played on LAN, while regional league play is
# treated as the other bucket. Lowercased for a case-insensitive match.
LAN_MARKERS = (
    "masters", "champions", "lock//in", "lock in", "last chance", "lcq",
)


def is_lan_event(event_name):
    """Best-effort guess of whether an event was played on LAN, from its name.

    True when the name carries an international-LAN marker. A blank name is not
    LAN. This is a heuristic over event text, not a stored flag, so the UI labels
    it as inferred.
    """
    if not event_name:
        return False
    low = event_name.casefold()
    return any(marker in low for marker in LAN_MARKERS)


@dataclass(frozen=True)
class EventFilter:
    """A LAN/online filter that ANDs into a query the same way DateWindow does.

    mode is "all" (no filter), "lan" (only inferred LAN events), or "online"
    (everything else, including events with no name). The LAN test mirrors
    is_lan_event in SQL. Like DateWindow this is all-qmark, since SQLite forbids
    mixing named and positional parameters in one statement.
    """
    mode: str = "all"

    def clause(self, column="event_name"):
        if self.mode not in ("lan", "online"):
            return ("1=1", [])
        expr = f"LOWER(COALESCE({column}, ''))"
        likes = " OR ".join(f"{expr} LIKE ?" for _ in LAN_MARKERS)
        params = [f"%{m}%" for m in LAN_MARKERS]
        if self.mode == "lan":
            return (f"({likes})", params)
        return (f"NOT ({likes})", params)


@dataclass(frozen=True)
class DateWindow:
    start: date | None = None
    end: date | None = None

    @classmethod
    def all_time(cls):
        return cls(None, None)

    @property
    def is_all_time(self):
        return self.start is None and self.end is None

    def clause(self, column="date"):
        """Return (sql, params) to AND into a query filtering on `column`.

        `column` is a fixed identifier supplied by our own code, never user
        input, so interpolating it is safe. The params carry the bound values.
        An open side contributes no condition; an all-time window returns the
        always-true "1=1" so the caller never has to branch.
        """
        conds, params = [], []
        if self.start is not None:
            conds.append(f"{column} >= ?")
            params.append(self.start.isoformat())
        if self.end is not None:
            conds.append(f"{column} <= ?")
            params.append(self.end.isoformat())
        return (" AND ".join(conds) if conds else "1=1", params)

    def contains(self, value):
        """True if a date (or ISO date string) falls within the window.

        This mirrors the SQL clause for Python-side use and tests. A None value
        stands for a row with no stored date: a bounded clause excludes it (NULL
        comparisons are false in SQL), and the all-time clause keeps it, so we
        return True only for the all-time window.
        """
        if value is None:
            return self.is_all_time
        if isinstance(value, str):
            value = date.fromisoformat(value)
        if self.start is not None and value < self.start:
            return False
        if self.end is not None and value > self.end:
            return False
        return True

    @property
    def label(self):
        if self.is_all_time:
            return "all time"
        start = self.start.isoformat() if self.start else "start"
        end = self.end.isoformat() if self.end else "now"
        return f"{start} to {end}"
