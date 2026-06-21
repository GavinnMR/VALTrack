"""Small parsing and normalization helpers for vlrggapi responses.

These are pure functions with no I/O so they can be unit tested with known
inputs and asserted outputs. The encoding fix matters because vlrggapi returns
text that has been UTF-8 encoded and then re-decoded as cp1252, so accented team
names arrive as mojibake until repaired.
"""
import re


def fix_encoding(value):
    """Repair the cp1252-over-utf8 mojibake vlrggapi returns.

    Many strings come back double-encoded, for example the en dash and accented
    team names. The original UTF-8 bytes were decoded as cp1252 with undefined
    bytes kept as surrogate escapes (so an accented name arrives mangled with a
    stray surrogate). Encoding back through cp1252 with surrogateescape and UTF-8
    recovers the original text. If it does not round-trip cleanly we leave it.
    """
    if not isinstance(value, str):
        return value
    try:
        return value.encode("cp1252", "surrogateescape").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def parse_score(score):
    """Split a "1:2" series score into (team1, team2) integers.

    Returns (None, None) when the score is missing or not yet decided.
    """
    if not score or ":" not in score:
        return (None, None)
    left, _, right = score.partition(":")
    try:
        return (int(left.strip()), int(right.strip()))
    except ValueError:
        return (None, None)


def parse_date(date):
    """Normalize a "YYYY/MM/DD" match date to "YYYY-MM-DD".

    Returns None when the value is empty or not in the expected shape, so a
    weird value never silently becomes a misleading date.
    """
    if not date:
        return None
    cleaned = date.strip().replace("/", "-")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cleaned):
        return cleaned
    return None


def parse_int(value):
    """Pull the first integer out of a string like "1" or "#3". None if absent."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    match = re.search(r"-?\d+", str(value))
    return int(match.group()) if match else None
