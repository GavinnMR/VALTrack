"""Date windowing for VALTrack.

The comparison runs over a user-chosen date range, all time by default. This is
the one place that knows how a window turns into a SQL filter, so every derived
statistic shares it: build a DateWindow once, then AND its
clause into whatever query reads from a dated table.

A window is a pair of inclusive bounds, each optional. All time leaves both
bounds open. The clause is always a valid boolean expression, so callers can
drop it straight into a WHERE without special-casing the all-time case.
"""
import re
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


# Stage classification (event-stage filter). The round label tells us whether a
# match is group/swiss play or a playoff/elimination bracket game, but VLR's
# labels vary wildly across years and formats (clean tokens like "UBSF", event
# names concatenated onto the round like "VCT NA S1: MastersGF", and full words
# like "Lower Bracket Final"). So this is a heuristic, mirroring is_lan_event:
# confident labels land in a bucket, anything ambiguous stays unclassified and is
# left out of both buckets rather than guessed into one.

# Whole-word substrings that confidently mark a playoff or single-elimination
# bracket game, checked against the lowercased round text (and the event name as
# a backup). Elimination and deciders count as playoff: they are knockout games.
_PLAYOFF_WORDS = (
    "playoff", "play-off", "play off", "grand final", "grand-final",
    "lower bracket", "upper bracket", "lower round", "upper round",
    "quarterfinal", "quarter-final", "quarter final",
    "semifinal", "semi-final", "semi final",
    "elimination", "elim", "knockout", "knock-out", "round of", "decider",
    "bracket", "3rd place", "third place", "bronze", "final round",
)
# Bracket abbreviations that are three or more characters are safe to match
# anywhere in the round text, since they do not collide with real words and
# survive the occasional trailing "(B)" group letter ("UBF (B)"). The two-letter
# finals codes (uf/lf/gf/qf/sf/cf) are too easy to hit by accident, so those are
# anchored to the end of the round token instead, where VLR puts them (often
# glued onto an event name, "...MastersGF").
_PLAYOFF_ABBREV = re.compile(
    r"(ubqf|ubsf|ubro\d*|ubr\d*|ubf|lbf|lbr\d*|lr\d+|ur\d+|uro\d*|uqf|usf|"
    r"ro\d+|mbf|mr\d+)"
)
_PLAYOFF_SUFFIX = re.compile(r"(uf|lf|gf|qf|sf|cf)$")
# Whole-word substrings that confidently mark group or swiss play (the regular
# season and the 0-0 / winners side of a swiss stage, which are not knockout).
_GROUP_WORDS = (
    "group", "swiss", "round robin", "round-robin", "regular season",
    "opening", "winner", "week",
)
# Trailing abbreviations for group/swiss: a week token ("W3") or round robin.
_GROUP_SUFFIX = re.compile(r"(w\d+|rr)$")


def classify_stage(event_round, event_name=None):
    """Bucket a match into "group", "playoff", or None (unclassified).

    Reads the round label first and falls back to the event name. Playoff and
    elimination (including swiss deciders and elimination matches) are one bucket;
    group and swiss non-elimination play (regular season, opening, winners) are
    the other. Anything that does not match a confident marker returns None, so
    the caller can keep it out of both buckets rather than mislabel it.

    The order matters: playoff markers are checked before group markers, so a
    "Group A GF" (a group's bracket final) lands in playoff, which is the more
    specific and decision-relevant read.
    """
    parts = [p for p in (event_round, event_name) if p]
    if not parts:
        return None
    text = " ".join(parts).casefold().strip()
    token = (event_round or "").casefold().strip()
    if (any(word in text for word in _PLAYOFF_WORDS)
            or _PLAYOFF_ABBREV.search(token) or _PLAYOFF_SUFFIX.search(token)):
        return "playoff"
    if any(word in text for word in _GROUP_WORDS) or _GROUP_SUFFIX.search(token):
        return "group"
    return None


@dataclass(frozen=True)
class StageFilter:
    """A group/playoff filter that ANDs into a query like DateWindow does.

    mode is "all" (no filter), "group" (group and swiss play), or "playoff"
    (playoff and elimination brackets). The stage is precomputed once into the
    matches.match_stage column (see classify_stage and the startup backfill), so
    the SQL is a plain equality. A match the classifier could not place has a NULL
    stage and is therefore excluded from both the group and playoff buckets, the
    same way an unknown-event match is excluded from LAN and online; "all" still
    includes it.
    """
    mode: str = "all"

    def clause(self, column="match_stage"):
        if self.mode not in ("group", "playoff"):
            return ("1=1", [])
        return (f"{column} = ?", [self.mode])


@dataclass(frozen=True)
class EventFilter:
    """A LAN/online filter that ANDs into a query the same way DateWindow does.

    mode is "all" (no filter), "lan" (only inferred LAN events), or "online"
    (events with a known name that are not LAN). The environment is inferred from
    event_name, which only the per-match detail pass fills in, so a match with no
    stored event name has an unknown environment. Such matches are excluded from
    both the LAN and online buckets rather than guessed into one, so the split
    only ever covers matches we can actually classify; "all" still includes them.

    The LAN test mirrors is_lan_event in SQL. Like DateWindow this is all-qmark,
    since SQLite forbids mixing named and positional parameters in one statement.
    """
    mode: str = "all"

    def clause(self, column="event_name"):
        if self.mode not in ("lan", "online"):
            return ("1=1", [])
        expr = f"LOWER(COALESCE({column}, ''))"
        likes = " OR ".join(f"{expr} LIKE ?" for _ in LAN_MARKERS)
        params = [f"%{m}%" for m in LAN_MARKERS]
        # A LAN marker can only match a non-empty name, so "lan" already excludes
        # unknown-event rows. "online" must exclude them explicitly.
        if self.mode == "lan":
            return (f"({likes})", params)
        known = f"({column} IS NOT NULL AND {column} != '')"
        return (f"({known} AND NOT ({likes}))", params)


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
