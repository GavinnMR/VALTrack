"""Resolve the API's upcoming-match feed to franchise-versus-franchise pairs.

The whole job is predicting an upcoming match, so the tool should be able to read
this week's slate rather than make the user pick both teams by hand. The live
upcoming-matches endpoint returns scheduled segments (team1, team2, event,
timestamp); this turns those into the subset that is franchise team versus
franchise team, resolved to the stored team rows so a click can load the pair.

The matching is pure so it can be unit tested without the API: the app fetches
the segments, this resolves them, and the app renders the result.
"""
import datetime as dt

from valtrack.window import is_lan_event


def _tokens(team):
    """Casefolded name and tag tokens for a franchise team row or dict."""
    return {t for t in ((team["name"] or "").casefold(),
                        (team["tag"] or "").casefold()) if t}


def _resolve(api_name, teams):
    """The franchise team matching an API team name, or None.

    Exact name or tag matches win first, so a clean feed name maps unambiguously;
    a looser containment match (the feed using a short tag, or a trailing
    "Esports") is the fallback. None when nothing reasonable matches, so a
    non-franchise opponent simply drops the segment.
    """
    low = (api_name or "").casefold().strip()
    if not low:
        return None
    for team in teams:
        if low in _tokens(team):
            return team
    for team in teams:
        for tok in _tokens(team):
            if tok and (tok in low or low in tok):
                return team
    return None


def _segment_date(seg):
    """The UTC date of a scheduled segment from its unix timestamp, or None."""
    ts = seg.get("unix_timestamp")
    try:
        if ts:
            return dt.datetime.fromtimestamp(
                int(float(ts)), tz=dt.timezone.utc).date().isoformat()
    except (ValueError, OSError, OverflowError):
        return None
    return None


def franchise_upcoming(segments, teams):
    """Scheduled segments where both teams resolve to stored franchise teams.

    Each returned entry is {a, b, event, match_date, is_lan}, where a and b are
    the matched team rows (in the feed's order). A segment is skipped when either
    side does not resolve to a franchise team or both resolve to the same one (a
    loose match collision), so only real franchise-versus-franchise meetings come
    back. match_date is the UTC date from the timestamp, or None when absent. The
    series format is intentionally not inferred: it is only known once the match
    is played and carries a score.
    """
    out = []
    for seg in segments:
        a = _resolve(seg.get("team1"), teams)
        b = _resolve(seg.get("team2"), teams)
        if a is None or b is None or a["id"] == b["id"]:
            continue
        event = seg.get("match_event") or ""
        out.append({
            "a": a,
            "b": b,
            "event": event,
            "match_date": _segment_date(seg),
            "is_lan": is_lan_event(event),
        })
    return out
