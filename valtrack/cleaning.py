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


def parse_float(value):
    """Pull the first number out of a string like "1.32", "267", or "172.3".

    Returns None when there is no number, so a blank stat never becomes 0.0 and
    silently dilutes an average.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(match.group()) if match else None


def side_to_phase(side):
    """Map a VLR round side code to attack or defense.

    VLR reports the winning team's side as "t" or "ct". In Valorant the T side
    attacks and the CT side defends, so we store "atk" / "def". Anything else
    returns None rather than guessing.
    """
    if side == "t":
        return "atk"
    if side == "ct":
        return "def"
    return None


def is_pistol_round(round_number):
    """A round is a pistol round when it opens a half: round 1 or round 13.

    Overtime rounds (25 and up) start with full eco rules, not a pistol, so they
    are not counted.
    """
    return round_number in (1, 13)


def parse_vetos(map_vetos):
    """Parse a VLR veto string into an ordered list of actions.

    The string looks like "PRX ban Abyss; KRX ban Pearl; PRX pick Haven; ...;
    Breeze remains". Each segment is either "<team> <ban|pick> <map>" or
    "<map> remains" for the leftover decider. Returns a list of dicts with seq,
    team_token, action, and map_name. The team_token is None for a "remains"
    segment. Returns an empty list for missing or unparseable input.
    """
    if not map_vetos or not isinstance(map_vetos, str):
        return []
    actions = []
    seq = 0
    for raw in map_vetos.split(";"):
        words = raw.split()
        if not words:
            continue
        if words[-1].lower() == "remains":
            team_token, action, map_name = None, "remains", " ".join(words[:-1])
        elif len(words) >= 3 and words[1].lower() in ("ban", "pick"):
            team_token, action, map_name = words[0], words[1].lower(), " ".join(words[2:])
        else:
            # An unexpected shape: keep the text as the map name so nothing is
            # silently dropped, but mark the action unknown.
            team_token, action, map_name = None, None, raw.strip()
        if not map_name:
            continue
        seq += 1
        actions.append(
            {"seq": seq, "team_token": team_token, "action": action, "map_name": map_name}
        )
    return actions
