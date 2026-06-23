"""Veto tendency aggregation and map-pool reconstruction (Build Step 11).

This is the flagship feature and the easiest to get subtly wrong, so the logic
is kept pure and transparent here and tested against hand-built inputs.

It does two things. First, it summarizes each team's veto tendencies from the
stored veto actions: per map, how often the team bans it, picks it, and how often
the map was in the pool at all. Second, given two teams' tendencies it
reconstructs a likely map pool for a hypothetical match between them: each team's
probable pick, the probable decider, and the maps likely to be banned.

It is a reconstruction from history, not a real upcoming veto. It never declares
a match winner; it surfaces which maps are likely and leaves the read to the
user, who then weighs the per-map win rates shown alongside.

The team in a veto action is stored as the VLR tag token (eg "PRX"), which is
resolved to a team by comparing against teams.tag, repairing the occasional
mojibake token first. Veto rows whose map is not a real Valorant map are dropped,
which also clears the junk the source sometimes puts in the veto field.
"""
from datetime import date, timedelta

from valtrack.cleaning import fix_encoding

# The Valorant maps that have been in competitive rotation. Aggregation is
# filtered to this set, so non-map junk in the veto field (status notes, broken
# segments from multi-word team names) is ignored rather than counted as a map.
CANON_MAPS = frozenset({
    "Abyss", "Ascent", "Bind", "Breeze", "Corrode", "Fracture",
    "Haven", "Icebox", "Lotus", "Pearl", "Split", "Sunset",
})


def _rate(part, whole):
    """A 0..1 rate, or None when there is nothing to divide (honest, not 0)."""
    return part / whole if whole else None


def _token_matches(token, tag):
    """True when a veto tag token refers to the team with this tag.

    The token comes straight from the veto string and can carry the cp1252
    mojibake the source returns, so it is repaired before comparing. Matching is
    case insensitive. A blank token or tag never matches.
    """
    if not token or not tag:
        return False
    return fix_encoding(token).strip().casefold() == str(tag).strip().casefold()


def _row_get(row, key):
    """Read a key from a sqlite Row or a plain dict, None when absent.

    The veto rows from the query carry a match date, but the hand-built test rows
    may not, so this tolerates a missing column rather than raising.
    """
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def team_tendencies(veto_rows, team_tag):
    """Summarize one team's veto tendencies per map.

    `veto_rows` are stored veto actions for the team's matches, each with
    match_id, team_token, action ("ban"/"pick"/"remains"), and map_name. Rows
    whose map is not a real Valorant map are skipped. For each remaining map:

      - appearances: distinct matches where the map was in the veto pool,
      - bans / picks: times this team (matched by tag) banned or picked it,
      - ban_rate / pick_rate: those counts over appearances (None when the map
        never appeared, so a missing map is not reported as 0%).

    Using appearances as the denominator keeps rates honest across map rotations:
    a map only in the pool for part of the window is judged over the matches it
    was actually available.
    """
    maps = {}
    for row in veto_rows:
        name = row["map_name"]
        if name not in CANON_MAPS:
            continue
        agg = maps.setdefault(
            name, {"matches": set(), "bans": 0, "picks": 0, "last_seen": None}
        )
        agg["matches"].add(row["match_id"])
        match_date = _row_get(row, "match_date")
        if match_date and (agg["last_seen"] is None or match_date > agg["last_seen"]):
            agg["last_seen"] = match_date
        action = row["action"]
        if action in ("ban", "pick") and _token_matches(row["team_token"], team_tag):
            agg[action + "s"] += 1
    out = {}
    for name, agg in maps.items():
        appearances = len(agg["matches"])
        out[name] = {
            "appearances": appearances,
            "bans": agg["bans"],
            "picks": agg["picks"],
            "ban_rate": _rate(agg["bans"], appearances),
            "pick_rate": _rate(agg["picks"], appearances),
            "last_seen": agg["last_seen"],
        }
    return out


def active_pool(tend_a, tend_b, size=7, recent_days=365):
    """Infer the current map pool from veto history, favoring recent maps.

    Rather than hardcode a pool that goes stale each patch, take the maps seen
    across both teams' tendencies. To keep an all-time window from dragging in
    maps that have rotated out, a map seen within recent_days of the latest veto
    in the data is preferred over an older one; among maps in the same recency
    bucket, the more frequently seen lead. When the tendencies carry no dates (as
    in hand-built tests), recency is ignored and this falls back to ranking by
    appearances. Returns up to `size` map names.
    """
    appearances = {}
    last_seen = {}
    for tend in (tend_a, tend_b):
        for name, agg in tend.items():
            appearances[name] = appearances.get(name, 0) + agg["appearances"]
            seen = agg.get("last_seen")
            if seen and (name not in last_seen or seen > last_seen[name]):
                last_seen[name] = seen

    cutoff = None
    if last_seen:
        latest = max(last_seen.values())
        cutoff = (date.fromisoformat(latest) - timedelta(days=recent_days)).isoformat()

    def is_recent(name):
        # No date for this map (or none at all): do not penalize it.
        if cutoff is None or name not in last_seen:
            return True
        return last_seen[name] >= cutoff

    ranked = sorted(
        appearances,
        key=lambda n: (0 if is_recent(n) else 1, -appearances[n], n),
    )
    return ranked[:size]


def _blank():
    return {"appearances": 0, "bans": 0, "picks": 0,
            "ban_rate": None, "pick_rate": None, "last_seen": None}


def reconstruct(tend_a, tend_b, pool):
    """Reconstruct a likely map pool for a hypothetical A versus B match.

    Mirrors a Bo3 veto over the given pool. For each map a play_score combines
    both teams' net inclination to play it (pick rate minus ban rate), so a map
    both teams pick and rarely ban scores high and a map both ban scores low.

    From that:
      - a_pick: A's highest pick-rate map,
      - b_pick: B's highest pick-rate map other than A's,
      - decider: the highest play_score map left after the two picks (the one
        neither side is likely to ban),
      - likely_bans: the rest, lowest play_score (most likely banned) first,
      - likely_played: a_pick, b_pick, and the decider.

    Returns those plus a per-map `rows` table (each team's pick and ban rates,
    appearances, and the play_score) sorted by play_score. Ties break
    deterministically by play_score then map name. Empty pool yields empty
    results rather than an error.
    """
    rows = []
    play = {}
    pick_a = {}
    pick_b = {}
    for name in pool:
        a = tend_a.get(name) or _blank()
        b = tend_b.get(name) or _blank()
        ap, ab = a["pick_rate"] or 0.0, a["ban_rate"] or 0.0
        bp, bb = b["pick_rate"] or 0.0, b["ban_rate"] or 0.0
        score = (ap - ab) + (bp - bb)
        play[name] = score
        pick_a[name] = ap
        pick_b[name] = bp
        # The most recent time either team had this map in a veto, so the view can
        # flag a map whose win-rate sample is frozen because it left the rotation
        # (a high win rate on a map untouched since a prior patch is exactly the
        # stale number the data-honesty principle exists to surface).
        seens = [s for s in (a.get("last_seen"), b.get("last_seen")) if s]
        rows.append({
            "map": name,
            "a_pick_rate": a["pick_rate"], "a_ban_rate": a["ban_rate"],
            "b_pick_rate": b["pick_rate"], "b_ban_rate": b["ban_rate"],
            "a_appearances": a["appearances"], "b_appearances": b["appearances"],
            "play_score": score,
            "last_seen": max(seens) if seens else None,
        })
    rows.sort(key=lambda r: (-r["play_score"], r["map"]))

    def best_pick(rates, exclude):
        cands = [n for n in pool if n not in exclude]
        if not cands:
            return None
        cands.sort(key=lambda n: (-rates[n], -play[n], n))
        return cands[0]

    a_pick = best_pick(pick_a, set())
    b_pick = best_pick(pick_b, {a_pick} if a_pick else set())

    chosen = {m for m in (a_pick, b_pick) if m}
    remaining = [n for n in pool if n not in chosen]
    remaining.sort(key=lambda n: (-play[n], n))
    decider = remaining[0] if remaining else None
    likely_bans = sorted(
        (n for n in remaining if n != decider),
        key=lambda n: (play[n], n),
    )
    likely_played = [m for m in (a_pick, b_pick, decider) if m]

    return {
        "rows": rows,
        "a_pick": a_pick,
        "b_pick": b_pick,
        "decider": decider,
        "likely_bans": likely_bans,
        "likely_played": likely_played,
    }
