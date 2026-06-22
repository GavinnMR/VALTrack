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
