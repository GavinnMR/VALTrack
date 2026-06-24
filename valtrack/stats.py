"""Pure derivations for VALTrack.

These take plain rows or lists and return computed figures with no database
access, so they are cheap to unit test against known inputs. The later
must-aggregate steps (side splits, pistol, opening duels) can add their pure
logic here too.
"""
import math
import re

from valtrack.agents import ROLE_ORDER, agent_role
from valtrack.cleaning import parse_float


def _rate(won, total):
    """A win rate as a 0..1 float, or None when there is nothing to divide.

    A None rate is honest: it says we have no rounds (or no decided maps) to
    judge a side on, rather than printing a misleading 0%.
    """
    return won / total if total else None

# Role text that marks a non-playing staff member. The stored is_staff flag is
# unusable (it comes back 0 for everyone) and the role text is sometimes mangled
# (a stand-in can show up as "Wongstand-in"), so we match on substrings and
# accept that the split is best effort.
_STAFF_MARKERS = ("coach", "manager", "analyst", "staff", "owner", "director")
_SUB_MARKERS = ("stand-in", "standin", "substitute")


def is_small_sample(n, threshold):
    """True when a count rests on too few observations to trust.

    A figure over very few rounds, maps, or matches can swing wildly, so the UI
    flags it rather than presenting it as if it were solid. A None or zero count
    is treated as small. The threshold is the smallest count still considered
    enough, so n below it is flagged.
    """
    return (n or 0) < threshold


def form_and_streak(results, n=5):
    """Summarize recent results into a short form list and the current streak.

    `results` is decided outcomes ordered newest first, each "W" or "L". The
    caller filters out ties and undecided matches, so it controls what counts.
    Returns a dict:
      - form: the most recent n results, still newest first
      - streak_kind: "W", "L", or None when there are no results
      - streak_len: how many of the most recent results share that kind
      - decided: how many results were supplied (the sample size)
    """
    streak_kind = results[0] if results else None
    streak_len = 0
    for r in results:
        if r == streak_kind:
            streak_len += 1
        else:
            break
    return {
        "form": results[:n],
        "streak_kind": streak_kind,
        "streak_len": streak_len,
        "decided": len(results),
    }


def classify_roster(rows):
    """Split a stored roster into the current five, stand-ins, and staff.

    Each row needs alias, real_name, role, and is_captain. Classification leans
    on the role text because the stored is_staff flag is unusable. A blank role
    is a main player. This is heuristic and can misplace someone when VLR's role
    text is odd, which the UI surfaces rather than hides.
    """
    mains, subs, staff = [], [], []
    for row in rows:
        role = (row["role"] or "").strip()
        folded = role.casefold()
        member = {
            "alias": row["alias"],
            "real_name": row["real_name"],
            "role": role,
            "is_captain": bool(row["is_captain"]),
        }
        if any(m in folded for m in _STAFF_MARKERS):
            staff.append(member)
        elif any(m in folded for m in _SUB_MARKERS):
            subs.append(member)
        else:
            mains.append(member)
    return {"mains": mains, "subs": subs, "staff": staff}


def current_five_names(roster_rows):
    """The casefolded aliases of a team's current five, for filtering by player.

    Takes the same stored roster rows classify_roster reads and returns just the
    main players' aliases, lowercased so a name match against the detail tables
    is case insensitive. Stand-ins and staff are left out, since the filter is
    meant to narrow stats to the five who currently start.
    """
    mains = classify_roster(roster_rows)["mains"]
    return {(m["alias"] or "").casefold() for m in mains if m["alias"]}


def keep_players(rows, names):
    """Filter per-player rows to a set of casefolded player names.

    Used by the current-five toggle: when `names` is given, only rows whose
    player_name is in the set survive, so the aggregations downstream see just
    those players. When `names` is None the rows pass through unchanged, so the
    toggle off path costs nothing. A name not in the detail tables simply drops
    out, which is honest rather than inventing a line for a player with no maps.
    """
    if names is None:
        return rows
    return [r for r in rows if (r["player_name"] or "").casefold() in names]


def map_winrates(map_rows, team_name):
    """Per-map win record for one team from stored map results.

    Each row needs map_name, winner_name, team1_name, and team2_name. The caller
    scopes the rows to maps this team played, so every row counts toward one map.
    A map with no winner_name (a forfeit or unplayed map) is not decided, so it
    adds to neither wins nor losses and drops out of the win rate denominator.

    Returns a dict keyed by map name, each value {won, lost, winrate}, where
    winrate is over decided maps (None when the team has no decided map there).
    """
    out = {}
    for row in map_rows:
        name = row["map_name"]
        if name is None:
            continue
        agg = out.setdefault(name, {"won": 0, "lost": 0})
        winner = row["winner_name"]
        if winner is None:
            continue
        if winner == team_name:
            agg["won"] += 1
        else:
            agg["lost"] += 1
    for agg in out.values():
        agg["winrate"] = _rate(agg["won"], agg["won"] + agg["lost"])
    return out


def side_winrates(round_rows, team_name):
    """Per-map attack and defense round win rates for one team.

    Each row needs map_name, winner_side ("atk" or "def"), and winner_team. The
    rounds table stores only the winner of each round, but the two teams are
    always on opposite sides, so a team's full side record follows from the
    winners alone:

      - a round this team won on attack is an attack round won,
      - a round the opponent won on defense is an attack round this team lost
        (the opponent defending means this team was attacking),

    and the mirror for defense. The caller scopes the rows to maps this team
    played, so "winner_team is not this team" is unambiguously the opponent.

    Returns a dict keyed by map name, each value with attack and defense wins,
    totals, and rates ({atk_won, atk_total, atk_winrate, def_won, def_total,
    def_winrate}); a rate is None when that side has no rounds.
    """
    out = {}
    for row in round_rows:
        name = row["map_name"]
        if name is None:
            continue
        side = row["winner_side"]
        if side not in ("atk", "def"):
            continue
        agg = out.setdefault(
            name, {"atk_won": 0, "atk_lost": 0, "def_won": 0, "def_lost": 0}
        )
        won_by_team = row["winner_team"] == team_name
        if side == "atk" and won_by_team:
            agg["atk_won"] += 1
        elif side == "def" and not won_by_team:
            agg["atk_lost"] += 1
        elif side == "def" and won_by_team:
            agg["def_won"] += 1
        else:  # side == "atk" and not won_by_team
            agg["def_lost"] += 1
    result = {}
    for name, agg in out.items():
        atk_total = agg["atk_won"] + agg["atk_lost"]
        def_total = agg["def_won"] + agg["def_lost"]
        result[name] = {
            "atk_won": agg["atk_won"],
            "atk_total": atk_total,
            "atk_winrate": _rate(agg["atk_won"], atk_total),
            "def_won": agg["def_won"],
            "def_total": def_total,
            "def_winrate": _rate(agg["def_won"], def_total),
        }
    return result


def pistol_winrate(round_rows, team_name):
    """Team-level pistol-round win rate, overall and split by side.

    Each row needs is_pistol, winner_side ("atk" or "def"), and winner_team. A
    pistol round is the first round of each half (round 1 and round 13), flagged
    as is_pistol in the stored rounds. As with the side splits, the rounds table
    holds only the winner of each round, but the two teams sit on opposite sides,
    so this team's pistol record follows from the winners alone:

      - a pistol this team won on attack is an attack pistol won,
      - a pistol the opponent won on defense is an attack pistol this team lost,

    and the mirror for defense. The caller scopes the rows to maps this team
    played, so "winner_team is not this team" is unambiguously the opponent.

    Unlike the per-map splits, pistol win rate is reported at team level only:
    each map carries one or two pistols, so a per-map pistol figure would rest on
    a sample too thin to mean anything. The won and total counts come back so the
    overall sample is visible.

    Returns a dict with overall {won, total, winrate} and attack and defense
    counterparts ({atk_won, atk_total, atk_winrate, def_won, def_total,
    def_winrate}); a rate is None when there are no pistols on that side.
    """
    won = total = 0
    atk_won = atk_total = def_won = def_total = 0
    for row in round_rows:
        if not row["is_pistol"]:
            continue
        side = row["winner_side"]
        if side not in ("atk", "def"):
            continue
        won_by_team = row["winner_team"] == team_name
        total += 1
        if won_by_team:
            won += 1
        # The pistol is an attack pistol for this team when this team won it on
        # attack or the opponent won it on defense, and a defense pistol the
        # other way round.
        if (side == "atk") == won_by_team:
            atk_total += 1
            if won_by_team:
                atk_won += 1
        else:
            def_total += 1
            if won_by_team:
                def_won += 1
    return {
        "won": won,
        "total": total,
        "winrate": _rate(won, total),
        "atk_won": atk_won,
        "atk_total": atk_total,
        "atk_winrate": _rate(atk_won, atk_total),
        "def_won": def_won,
        "def_total": def_total,
        "def_winrate": _rate(def_won, def_total),
    }


def _duel_block(fk, fd, atk_fk, atk_fd, def_fk, def_fd):
    """Shape one team's or player's opening-duel counts into a display dict.

    Opening-duel win rate is first kills over opening duels (first kills plus
    first deaths): of the round-opening fights this team or player was in, the
    share that went their way. The side splits are the same ratio over the
    attack-side and defense-side duels. A rate is None when there are no duels on
    that side, which is honest rather than printing 0%.
    """
    duels = fk + fd
    atk_duels = atk_fk + atk_fd
    def_duels = def_fk + def_fd
    return {
        "fk": fk,
        "fd": fd,
        "duels": duels,
        "winrate": _rate(fk, duels),
        "atk_fk": atk_fk,
        "atk_fd": atk_fd,
        "atk_duels": atk_duels,
        "atk_winrate": _rate(atk_fk, atk_duels),
        "def_fk": def_fk,
        "def_fd": def_fd,
        "def_duels": def_duels,
        "def_winrate": _rate(def_fk, def_duels),
    }


def opening_duels(player_rows, team_name):
    """Team and per-player opening-duel win rates, overall and split by side.

    Each row needs team_name, player_name, and the per-map first-kill and
    first-death counts: first_kills, first_deaths, and the per-side
    first_kills_atk / first_kills_def / first_deaths_atk / first_deaths_def. The
    counts are per-map totals (VLR does not expose per-round first-blood events),
    so this is a true attack and defense split but not a round-by-round timeline.

    Rows for the opponent are ignored, so the caller can pass every player on a
    map. A null count is treated as zero. Returns a dict with the team totals
    (see _duel_block) and a "players" list of the same shape per player, sorted
    by opening duels descending then player name, so the entry duelists lead.
    """
    def num(value):
        return value or 0

    team = {
        "fk": 0, "fd": 0, "atk_fk": 0, "atk_fd": 0, "def_fk": 0, "def_fd": 0,
    }
    per_player = {}
    for row in player_rows:
        if row["team_name"] != team_name:
            continue
        name = row["player_name"]
        agg = per_player.setdefault(
            name,
            {"fk": 0, "fd": 0, "atk_fk": 0, "atk_fd": 0, "def_fk": 0, "def_fd": 0},
        )
        for key, src in (
            ("fk", "first_kills"), ("fd", "first_deaths"),
            ("atk_fk", "first_kills_atk"), ("atk_fd", "first_deaths_atk"),
            ("def_fk", "first_kills_def"), ("def_fd", "first_deaths_def"),
        ):
            v = num(row[src])
            agg[key] += v
            team[key] += v

    players = [
        {"player_name": name, **_duel_block(
            a["fk"], a["fd"], a["atk_fk"], a["atk_fd"], a["def_fk"], a["def_fd"]
        )}
        for name, a in per_player.items()
    ]
    players.sort(key=lambda p: (-p["duels"], p["player_name"]))

    result = _duel_block(
        team["fk"], team["fd"], team["atk_fk"], team["atk_fd"],
        team["def_fk"], team["def_fd"],
    )
    result["players"] = players
    return result


def _weighted_mean(pairs):
    """Round-weighted mean of (value, weight) pairs, skipping missing pieces.

    A pair contributes only when both the value and a positive weight are
    present, so a blank stat or a map with an unknown round count never dilutes
    the average. Returns None when nothing contributes, which is honest rather
    than a fabricated 0. This is how the per-round rate stats (rating, ACS, ADR,
    KAST, headshot percentage) are combined across a player's maps: each map's
    figure is weighted by the rounds it was earned over, the way VLR itself sums
    a player's season average.
    """
    num = den = 0.0
    for value, weight in pairs:
        if value is None or not weight:
            continue
        num += value * weight
        den += weight
    return num / den if den else None


def _spread(values):
    """Min, max, and population standard deviation of a list of numbers.

    Skips None entries, so a blank map drops out rather than counting as zero.
    The standard deviation is the population form (divided by n, not n-1), which
    is the right summary of the maps a player actually played rather than a sample
    estimate of a larger unobserved set. Returns all-None when fewer than two real
    values remain, since dispersion over zero or one map says nothing. This is the
    spread of one stat across maps, never a composite of several stats.
    """
    nums = [v for v in values if v is not None]
    if len(nums) < 2:
        lone = nums[0] if nums else None
        return {"min": lone, "max": lone, "std": None, "n": len(nums)}
    mean = sum(nums) / len(nums)
    var = sum((v - mean) ** 2 for v in nums) / len(nums)
    return {"min": min(nums), "max": max(nums), "std": math.sqrt(var),
            "n": len(nums)}


def _agent_block(agg):
    """Shape one agent's accumulated counts into a display dict."""
    return {
        "maps": agg["maps"],
        "kills": agg["kills"],
        "deaths": agg["deaths"],
        "kd": _rate(agg["kills"], agg["deaths"]),
        "rating": _weighted_mean(agg["rating"]),
        "acs": _weighted_mean(agg["acs"]),
    }


def player_aggregates(rows, team_name):
    """Per-player aggregated stat lines for one team across the windowed maps.

    Each row is one player on one map and needs team_name, player_name, agent,
    the per-map rating / acs / kills / deaths / assists / kast / adr / hs_pct,
    first_kills / first_deaths, and map_rounds (the map's total rounds). Rows for
    the other team are ignored, so the caller can pass every player on a map.

    Two kinds of figure come back. The counting stats are summed then divided,
    which is exact: K/D is total kills over total deaths, kills and assists per
    round are the totals over the rounds the player was on the server, and the
    same for first-kill and first-death rates. The per-round rate stats (rating,
    ACS, ADR, KAST percentage, headshot percentage) are round-weighted averages
    of the per-map figures (see _weighted_mean), since each was already a
    per-round number. KAST and headshot percentage arrive as text like "75%" and
    are parsed before weighting. Headshot percentage is round-weighted as an
    approximation: its true denominator is hits, which the source does not store.

    Clutch statistics are not produced: the data source does not expose them, so
    they are left out rather than invented.

    A None stat is skipped from its own average without zeroing it, and a map
    with an unknown round count drops out of the round-weighted and per-round
    figures. Returns a list of per-player dicts sorted by maps played descending
    then player name, each carrying the aggregated line, the maps and rounds
    sample sizes, an agent pool (maps per agent), and a per-agent breakdown.
    """
    def num(value):
        return value or 0

    players = {}
    for row in rows:
        if row["team_name"] != team_name:
            continue
        name = row["player_name"]
        acc = players.setdefault(name, {
            "player_id": row["player_id"],
            "maps": 0, "rounds": 0,
            "kills": 0, "deaths": 0, "assists": 0,
            "first_kills": 0, "first_deaths": 0,
            "rating": [], "acs": [], "adr": [], "kast": [], "hs": [],
            "agents": {},
        })
        rounds = row["map_rounds"]
        acc["maps"] += 1
        acc["rounds"] += num(rounds)
        acc["kills"] += num(row["kills"])
        acc["deaths"] += num(row["deaths"])
        acc["assists"] += num(row["assists"])
        acc["first_kills"] += num(row["first_kills"])
        acc["first_deaths"] += num(row["first_deaths"])
        acc["rating"].append((row["rating"], rounds))
        acc["acs"].append((row["acs"], rounds))
        acc["adr"].append((row["adr"], rounds))
        acc["kast"].append((parse_float(row["kast"]), rounds))
        acc["hs"].append((parse_float(row["hs_pct"]), rounds))

        agent = row["agent"]
        if agent:
            ag = acc["agents"].setdefault(
                agent, {"maps": 0, "kills": 0, "deaths": 0, "rating": [], "acs": []}
            )
            ag["maps"] += 1
            ag["kills"] += num(row["kills"])
            ag["deaths"] += num(row["deaths"])
            ag["rating"].append((row["rating"], rounds))
            ag["acs"].append((row["acs"], rounds))

    out = []
    for name, acc in players.items():
        agents = sorted(
            ((agent, _agent_block(ag)) for agent, ag in acc["agents"].items()),
            key=lambda pair: (-pair[1]["maps"], pair[0]),
        )
        out.append({
            "player_name": name,
            "player_id": acc["player_id"],
            "maps": acc["maps"],
            "rounds": acc["rounds"],
            "kills": acc["kills"],
            "deaths": acc["deaths"],
            "assists": acc["assists"],
            "first_kills": acc["first_kills"],
            "first_deaths": acc["first_deaths"],
            "rating": _weighted_mean(acc["rating"]),
            "acs": _weighted_mean(acc["acs"]),
            "adr": _weighted_mean(acc["adr"]),
            "kast": _weighted_mean(acc["kast"]),
            "hs_pct": _weighted_mean(acc["hs"]),
            # Per-map spread alongside the central tendency, so a steady player and
            # a feast-or-famine one are distinguishable (a 1.10 average that sits
            # 1.00-1.20 every map reads differently from one that swings 0.70-1.60).
            # This is the dispersion of one stat, not a new rating.
            "rating_spread": _spread([v for v, _ in acc["rating"]]),
            "acs_spread": _spread([v for v, _ in acc["acs"]]),
            "adr_spread": _spread([v for v, _ in acc["adr"]]),
            "kd": _rate(acc["kills"], acc["deaths"]),
            "kpr": _rate(acc["kills"], acc["rounds"]),
            "apr": _rate(acc["assists"], acc["rounds"]),
            "fk_per_round": _rate(acc["first_kills"], acc["rounds"]),
            "fd_per_round": _rate(acc["first_deaths"], acc["rounds"]),
            # Opening-duel win rate (first kills over opening duels) at player
            # level, the same ratio Build Step 8 reports, kept here so the
            # player-versus-player view in Build Step 10 can read it directly.
            "open_duels": acc["first_kills"] + acc["first_deaths"],
            "open_winrate": _rate(
                acc["first_kills"], acc["first_kills"] + acc["first_deaths"]
            ),
            "agents": [
                {"agent": agent, **block} for agent, block in agents
            ],
        })
    out.sort(key=lambda p: (-p["maps"], p["player_name"]))
    return out


def player_recent_ratings(rows, team_name, last_maps=10):
    """Each player's rating over their most recent maps, for a form trajectory.

    The per-player table shows a window average and the per-map spread, but not
    direction: one star heating up or going cold swings a match, and that is
    invisible in a flat average. This takes the same per-map player rows
    player_aggregates reads (each needs team_name, player_name, match_date,
    rating, and map_rounds) and, per player, round-weights the rating over only
    their `last_maps` most recent maps (newest by match_date), so it can be shown
    beside the full-window rating with the delta.

    Rows for the other team are ignored. A map with no date sorts oldest so it
    never crowds out a dated recent map. Returns a dict keyed by player_name, each
    value {recent_rating, recent_maps}; recent_rating is None when none of the
    recent maps carried a rating. This is one player's own figure and its trend,
    never a composite across players or a winner call.
    """
    by_player = {}
    for row in rows:
        if row["team_name"] != team_name:
            continue
        by_player.setdefault(row["player_name"], []).append(row)
    out = {}
    for name, prows in by_player.items():
        prows = sorted(prows, key=lambda r: (r["match_date"] or ""), reverse=True)
        recent = prows[:last_maps]
        rating = _weighted_mean((r["rating"], r["map_rounds"]) for r in recent)
        out[name] = {"recent_rating": rating, "recent_maps": len(recent)}
    return out


def per_map_splits(map_rows, round_rows, team_name):
    """Combine the map win record and side splits into one per-map table.

    Returns a list of per-map dicts ready for display, each carrying the map
    name, the win-loss record and map win rate, the attack and defense win rates
    with their round counts, and the total decided rounds. Maps are ordered by
    how many decided maps the team has on them (most first), then by name, so the
    maps the team plays most sit at the top.
    """
    maps = map_winrates(map_rows, team_name)
    sides = side_winrates(round_rows, team_name)
    rows = []
    for name in maps.keys() | sides.keys():
        m = maps.get(name, {"won": 0, "lost": 0, "winrate": None})
        s = sides.get(
            name,
            {"atk_won": 0, "atk_total": 0, "atk_winrate": None,
             "def_won": 0, "def_total": 0, "def_winrate": None},
        )
        rows.append({
            "map_name": name,
            "won": m["won"],
            "lost": m["lost"],
            "map_winrate": m["winrate"],
            "atk_won": s["atk_won"],
            "atk_total": s["atk_total"],
            "atk_winrate": s["atk_winrate"],
            "def_won": s["def_won"],
            "def_total": s["def_total"],
            "def_winrate": s["def_winrate"],
            "rounds_total": s["atk_total"] + s["def_total"],
        })
    rows.sort(key=lambda r: (-(r["won"] + r["lost"]), r["map_name"]))
    return rows


def player_map_aggregates(rows, team_name):
    """Per-player stat lines for one team, split out by map.

    Crosses the per-player figures with the map they were earned on, so a player
    who pops off on Ascent but goes quiet on Lotus shows two different lines
    rather than one blended average. Each row is the same shape player_aggregates
    reads (it carries map_name from the query), so this groups the rows by map and
    runs the exact same round-weighted aggregation per map, reusing that tested
    logic untouched.

    Returns a dict keyed by map name, each value the player_aggregates list for
    that map (already sorted by maps played then name). Rows with no map name are
    skipped, since a line with no map cannot be placed. Per-player-per-map samples
    are thin, so the caller flags small ones the same way it does elsewhere; that
    is the point of splitting by map, not a footnote.
    """
    by_map = {}
    for row in rows:
        name = row["map_name"]
        if not name:
            continue
        by_map.setdefault(name, []).append(row)
    return {m: player_aggregates(rs, team_name) for m, rs in by_map.items()}


def team_rating(players):
    """One round-weighted team rating from a player_aggregates list, or None.

    Each player already carries a round-weighted rating and a rounds count, so the
    team figure weights each player's rating by the rounds they played, the same
    way the per-player rating weighted its maps. A player with no rating or no
    rounds drops out rather than pulling the average toward zero. Returns None when
    nothing contributes, which is honest rather than a fabricated 0. This is a
    headline summary number, not a ranking: it is shown beside the opponent's with
    the gap, never folded into a winner call.
    """
    return _weighted_mean((p["rating"], p["rounds"]) for p in players)


def pressure_stats(rows, team_name):
    """Decider, distance, and comeback figures for one team's series.

    `rows` are the per-map results for the team's matches, each with match_id,
    map_order, winner_name (the map winner), and the series scores from the team's
    point of view (team_series_score and opp_series_score, identical on every row
    of a match). Rows with no map_order are skipped from the ordering.

    The series format (Bo3 versus Bo5) is not stored, so the definitions avoid
    assuming one:
      - decider: the final map of a series entered level on maps, with at least
        one map won by each side going in. A sweep's last map (not level going in)
        and a lone Bo1 map (no map won by each side) are therefore not deciders.
      - decider win%: the team won that deciding map, over deciders played.
      - distance win%: the team won the series, over deciders played (series
        outcome read from the series score; closely related to the decider map
        result, kept as a separate series-level figure rather than folded in).
      - comeback: lost the opening map but still won the series, reported as a
        count over the series where the team lost map 1 (the comeback chances).

    These come back as separate figures on purpose. They are never combined into a
    single "clutch" or "resilience" rating, which would be the composite the
    charter forbids.
    """
    series = {}
    for row in rows:
        sid = row["match_id"]
        s = series.setdefault(sid, {
            "maps": [],
            "team_series": row["team_series_score"],
            "opp_series": row["opp_series_score"],
        })
        s["maps"].append(row)

    decider_played = decider_won = distance_won = 0
    comeback_chances = comeback_won = 0
    for s in series.values():
        maps = sorted(
            (m for m in s["maps"] if m["map_order"] is not None),
            key=lambda m: m["map_order"],
        )
        if not maps:
            continue
        team_series, opp_series = s["team_series"], s["opp_series"]
        series_decided = (
            team_series is not None and opp_series is not None
            and team_series != opp_series
        )
        won_series = series_decided and team_series > opp_series

        # A comeback is dropping the opening map and still taking the series.
        first = maps[0]["winner_name"]
        if series_decided and first is not None and first != team_name:
            comeback_chances += 1
            if won_series:
                comeback_won += 1

        # The decider is the final map when the map score was level going into it,
        # with at least one map already won by each side (so a sweep is excluded).
        team_maps = opp_maps = 0
        for m in maps[:-1]:
            winner = m["winner_name"]
            if winner is None:
                continue
            if winner == team_name:
                team_maps += 1
            else:
                opp_maps += 1
        if team_maps >= 1 and opp_maps >= 1 and team_maps == opp_maps:
            decider_played += 1
            if maps[-1]["winner_name"] == team_name:
                decider_won += 1
            if won_series:
                distance_won += 1

    return {
        "decider_played": decider_played,
        "decider_won": decider_won,
        "decider_winrate": _rate(decider_won, decider_played),
        "distance_played": decider_played,
        "distance_series_won": distance_won,
        "distance_winrate": _rate(distance_won, decider_played),
        "comeback_chances": comeback_chances,
        "comeback_won": comeback_won,
        "comeback_rate": _rate(comeback_won, comeback_chances),
    }


def map_pool_overlap(a_splits, b_splits, pool=None, strong_threshold=0.5):
    """Mark where two teams' per-map strengths collide and where they diverge.

    `a_splits` and `b_splits` are per-map dicts keyed by map name (as
    per_map_splits returns, reshaped by map), each value carrying map_winrate.
    `pool` restricts and orders the maps; when omitted, every map either team has
    is used, sorted by name. A map at or above strong_threshold is a strength for
    that team, below it a weakness.

    Each map is labeled descriptively and nothing more:
      - "shared strength": both teams strong (likely a coin flip there),
      - "shared weakness": both teams weak,
      - "split": one strong, one weak (the veto battle decides it),
      - "insufficient": either team has no decided map there to judge.

    This stays strictly descriptive. It shows each team's win rate and marks the
    map, and stops. It never ranks the maps into a "who wins the veto" answer,
    which would be the verdict the charter forbids.
    """
    names = pool if pool is not None else sorted(set(a_splits) | set(b_splits))
    out = []
    for name in names:
        a = a_splits.get(name)
        b = b_splits.get(name)
        a_win = a["map_winrate"] if a else None
        b_win = b["map_winrate"] if b else None
        if a_win is None or b_win is None:
            label = "insufficient"
        elif a_win >= strong_threshold and b_win >= strong_threshold:
            label = "shared strength"
        elif a_win < strong_threshold and b_win < strong_threshold:
            label = "shared weakness"
        else:
            label = "split"
        out.append({
            "map": name,
            "a_winrate": a_win,
            "b_winrate": b_win,
            "label": label,
        })
    return out


def primary_role(agent_pool):
    """Infer a player's role from their agent pool (most maps wins).

    `agent_pool` is the per-player agent list from player_aggregates, each entry
    {agent, maps, ...}. Each agent is mapped to a role (see valtrack.agents) and
    the maps are tallied per role; the role with the most maps is the player's.
    Agents the table does not know, and a player with no agents at all, fall
    under "unknown" rather than being guessed into a real role. Ties break by
    ROLE_ORDER so the result is deterministic.

    This is a best-effort inference: the source gives agent usage, not an
    explicit role, so a heavy flex player can land in a role they only narrowly
    favor. The view labels it as inferred.
    """
    tally = {}
    for entry in agent_pool:
        role = agent_role(entry["agent"]) or "unknown"
        tally[role] = tally.get(role, 0) + entry["maps"]
    if not tally:
        return "unknown"
    return min(tally, key=lambda role: (-tally[role], ROLE_ORDER.index(role)))


def wilson_interval(won, total, z=1.96):
    """A Wilson score confidence interval for a win rate, as (low, high) 0..1.

    The small-sample flag elsewhere is binary: a rate is either trusted or
    marked thin. This says how thin. The Wilson interval is the standard
    confidence band for a proportion and behaves well on small and lopsided
    samples (it never runs past 0 or 1 and is not silly at 0 or 100 percent),
    which is exactly the regime a thin VCT sample lives in. A 60 percent over 10
    rounds comes back as a wide band, a 60 percent over 200 as a tight one, so
    the user sees how reliable the number actually is.

    Returns None when there is nothing to judge (no observations), which is
    honest rather than a fabricated band. z defaults to 1.96 (about 95 percent).
    This is a spread around a single rate, never a comparison or a verdict.
    """
    if not total:
        return None
    phat = won / total
    denom = 1 + z * z / total
    centre = (phat + z * z / (2 * total)) / denom
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * total)) / total) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def bands_overlap(a_won, a_total, b_won, b_total):
    """Whether two rates' Wilson confidence bands overlap, so a gap is within noise.

    The per-row gap says which team is higher, but not whether the difference can
    be told apart from sampling noise. This builds each team's 95% Wilson interval
    and returns True when they intersect (the gap is not distinguishable at that
    confidence, for example 53% vs 49% over twenty matches), False when the two
    intervals are disjoint (a real, resolvable edge), and None when either side has
    no sample to build an interval from. It annotates a single per-statistic
    difference with how much weight it carries; it never tallies rows or calls a
    match winner.
    """
    a = wilson_interval(a_won, a_total)
    b = wilson_interval(b_won, b_total)
    if a is None or b is None:
        return None
    return a[0] <= b[1] and b[0] <= a[1]


def infer_match_format(team_score, opp_score):
    """Infer the series format (Bo1, Bo3, Bo5) from the series score.

    The format is not stored, but the maps needed to clinch fix it: a Bo5 is won
    at three maps, a Bo3 at two, a Bo1 at one. So the higher series score names
    the format without needing the map count or a format assumption. Returns None
    when either score is missing. Used to annotate a meeting so a 2-0 (Bo3) reads
    differently from a 3-0 (Bo5).
    """
    if team_score is None or opp_score is None:
        return None
    top = max(team_score, opp_score)
    if top >= 3:
        return "Bo5"
    if top == 2:
        return "Bo3"
    if top == 1:
        return "Bo1"
    return None


def map_compositions(rows, team_name):
    """The agent compositions one team runs per map, with how each one does.

    Crosses the per-map player stats with the map result to recover, for each
    map a team played, the five-agent composition it fielded and the record on
    it. The data model lists agent compositions per map but the app only shows
    per-player agent pools, so this assembles the team-level comp the veto read
    actually wants.

    Each row needs match_id, map_name, team_name, agent, and winner_name (the map
    winner). Rows for the other team are ignored. A composition is the sorted
    tuple of the agents that team fielded in one map instance; identical comps
    across maps are tallied together.

    Returns a dict keyed by map name, each value a list of
    {agents, played, won, winrate} sorted by how often the comp was played then
    its win rate. winrate is wins over times played (None when never decided).
    This is descriptive: it shows what a team brings and how it fared, never a
    pick recommendation.
    """
    games = {}
    for row in rows:
        if row["team_name"] != team_name:
            continue
        name = row["map_name"]
        if not name:
            continue
        key = (row["match_id"], name)
        g = games.setdefault(
            key, {"map": name, "agents": [], "winner": row["winner_name"]}
        )
        if row["agent"]:
            g["agents"].append(row["agent"])

    by_map = {}
    for g in games.values():
        comp = tuple(sorted(g["agents"]))
        tally = by_map.setdefault(g["map"], {})
        agg = tally.setdefault(comp, {"played": 0, "won": 0})
        agg["played"] += 1
        if g["winner"] == team_name:
            agg["won"] += 1

    out = {}
    for name, comps in by_map.items():
        rows_out = [
            {"agents": list(comp), "played": agg["played"], "won": agg["won"],
             "winrate": _rate(agg["won"], agg["played"])}
            for comp, agg in comps.items()
        ]
        rows_out.sort(key=lambda c: (-c["played"], -(c["winrate"] or 0)))
        out[name] = rows_out
    return out


def map_duel_board(a_splits, b_splits, pool=None):
    """Frame each map as the cross-side duel between two teams.

    Valorant maps are sided, so the read a predictor wants on a given map is not
    each team's attack and defense in isolation but the duel: team A attacking
    against team B defending, and the mirror. This lines those up.

    `a_splits` and `b_splits` are per-map dicts keyed by map name (as
    per_map_splits returns, reshaped by map), each carrying map_winrate,
    atk_winrate, def_winrate, and rounds_total. `pool` restricts and orders the
    maps (the likely-played pool); when omitted, every map either team has is
    used, sorted by name.

    Each row carries both teams' map win rate, both attack rates, both defense
    rates, and the round samples, so the view can pair A attack with B defense
    and B attack with A defense. A missing side is None, which the view shows as
    blank rather than a fabricated rate. This stays per-map and per-side; it
    never collapses to a "who wins the map" call.
    """
    names = pool if pool is not None else sorted(set(a_splits) | set(b_splits))
    out = []
    for name in names:
        a = a_splits.get(name)
        b = b_splits.get(name)
        out.append({
            "map": name,
            "a_map": a["map_winrate"] if a else None,
            "b_map": b["map_winrate"] if b else None,
            "a_atk": a["atk_winrate"] if a else None,
            "a_def": a["def_winrate"] if a else None,
            "b_atk": b["atk_winrate"] if b else None,
            "b_def": b["def_winrate"] if b else None,
            "a_rounds": (a["rounds_total"] if a else 0),
            "b_rounds": (b["rounds_total"] if b else 0),
        })
    return out


def rank_metric_gaps(metrics):
    """Order comparable metrics by the size of the gap between two teams.

    `metrics` is a list of dicts each with at least "metric", "a", and "b" (the
    two teams' values, already on a common scale, or None). Each row gains a
    signed gap (a minus b), its absolute size, and a "leader" tag ("a", "b", or
    None on a tie or a missing side). Rows are sorted by absolute gap descending,
    with rows missing a value last. Extra keys on the input (a suffix, decimals)
    pass through untouched for the view.

    This surfaces where two teams differ most and where they are nearly even,
    which is the read a predictor assembles by hand. The hard line: this sorts
    per-statistic differences by size and tags which side leads each row. It does
    not count how many a team leads or produce any overall winner, which would be
    the composite the charter forbids.
    """
    out = []
    for m in metrics:
        a, b = m.get("a"), m.get("b")
        gap = None if a is None or b is None else a - b
        leader = None
        if gap is not None and gap != 0:
            leader = "a" if gap > 0 else "b"
        out.append({
            **m,
            "gap": gap,
            "abs_gap": abs(gap) if gap is not None else None,
            "leader": leader,
        })
    out.sort(key=lambda r: (r["abs_gap"] is None, -(r["abs_gap"] or 0)))
    return out


def economy_conversion(rows, team_name):
    """Win rate by buy type for one team, from stored aggregate economy.

    Each row needs team_name, buy_type ("eco", "light", "half", "full"), played,
    and won (VLR's per-map buy-type table, summed over the window). Rows for the
    other team are ignored. Returns a dict keyed by buy type, each value
    {won, total, winrate} where total is rounds played of that type. The eco
    bucket's win rate is the eco conversion (how often a team wins a round it
    could not fully buy); the figures are reported per buy type and never folded
    into one economy rating.
    """
    buckets = {}
    for row in rows:
        if row["team_name"] != team_name:
            continue
        bt = row["buy_type"]
        if not bt:
            continue
        agg = buckets.setdefault(bt, {"won": 0, "total": 0})
        agg["won"] += row["won"] or 0
        agg["total"] += row["played"] or 0
    return {
        bt: {"won": agg["won"], "total": agg["total"],
             "winrate": _rate(agg["won"], agg["total"])}
        for bt, agg in buckets.items()
    }


def clutch_stats(rows, team_name):
    """Team and per-player clutch (1vX) wins from stored performance counts.

    Each row needs player_name, team_name, and clutch_1v1..clutch_1v5: the 1vX
    situations the player won, by depth. Rows for the other team are ignored; null
    counts are treated as zero. Returns team totals {won, by_depth} plus a
    "players" list (player_name, won, by_depth, deepest), sorted by clutches won
    descending then name. by_depth is a dict keyed 1..5; deepest is the hardest
    clutch the player closed, or 0 for none.

    VLR exposes clutches won by situation only, not attempts or losses, so this
    reports won counts and the 1v1..1v5 distribution and deliberately derives no
    clutch win rate (a rate needs attempts, which are not available). Counts, not
    a rating.
    """
    depths = (1, 2, 3, 4, 5)
    team_by = {d: 0 for d in depths}
    per = {}
    for row in rows:
        if row["team_name"] != team_name:
            continue
        agg = per.setdefault(row["player_name"], {d: 0 for d in depths})
        for d in depths:
            value = row[f"clutch_1v{d}"] or 0
            agg[d] += value
            team_by[d] += value
    players = []
    for name, by in per.items():
        won = sum(by.values())
        deepest = max((d for d in depths if by[d]), default=0)
        players.append({"player_name": name, "won": won,
                        "by_depth": dict(by), "deepest": deepest})
    players.sort(key=lambda p: (-p["won"], p["player_name"]))
    return {"won": sum(team_by.values()), "by_depth": team_by, "players": players}


def multikill_stats(rows, team_name):
    """Per-player multikill counts (2K..5K) for one team, from performance rows.

    Each row needs player_name, team_name, and mk_2k..mk_5k. Rows for the other
    team are ignored; null counts are treated as zero. Returns a per-player list
    (player_name, k2, k3, k4, k5, total) sorted by the rarer kills first (5K, then
    4K, 3K, 2K) so the standout rounds surface, then name. Counts that separate a
    star who wins rounds in bursts from one whose fragging is spread thin, never a
    rating.
    """
    per = {}
    for row in rows:
        if row["team_name"] != team_name:
            continue
        agg = per.setdefault(
            row["player_name"], {"k2": 0, "k3": 0, "k4": 0, "k5": 0})
        agg["k2"] += row["mk_2k"] or 0
        agg["k3"] += row["mk_3k"] or 0
        agg["k4"] += row["mk_4k"] or 0
        agg["k5"] += row["mk_5k"] or 0
    out = [{"player_name": name, **a,
            "total": a["k2"] + a["k3"] + a["k4"] + a["k5"]}
           for name, a in per.items()]
    out.sort(key=lambda p: (-p["k5"], -p["k4"], -p["k3"], -p["k2"],
                            p["player_name"]))
    return out


def utility_stats(rows, team_name):
    """Per-player plant and defuse counts for one team, from performance rows.

    Each row needs player_name, team_name, plants, and defuses. Rows for the other
    team are ignored; null counts are treated as zero. Returns team totals
    {plants, defuses} plus a "players" list (player_name, plants, defuses) sorted
    by plants plus defuses descending then name. Counts that hint at post-plant and
    retake roles, never a quality rating.
    """
    per = {}
    total_plants = total_defuses = 0
    for row in rows:
        if row["team_name"] != team_name:
            continue
        agg = per.setdefault(row["player_name"], {"plants": 0, "defuses": 0})
        agg["plants"] += row["plants"] or 0
        agg["defuses"] += row["defuses"] or 0
        total_plants += row["plants"] or 0
        total_defuses += row["defuses"] or 0
    players = [{"player_name": name, **a} for name, a in per.items()]
    players.sort(key=lambda p: (-(p["plants"] + p["defuses"]), p["player_name"]))
    return {"plants": total_plants, "defuses": total_defuses, "players": players}


def round_win_conditions(rows):
    """Aggregate a team's round wins by win condition and side.

    `rows` are (winner_side, win_type, n) counts as team_round_win_types returns.
    Returns {by_type, by_side, total}: total wins per condition (elim, defuse,
    time, boom), the same split by attack and defense, and the overall total. It
    shows how a team tends to close rounds (a defense winning by time or defuse
    plays differently from one that wins by elimination), as descriptive counts,
    never a quality score.
    """
    types = ("elim", "defuse", "time", "boom")
    by_type = {t: 0 for t in types}
    by_side = {"atk": {t: 0 for t in types}, "def": {t: 0 for t in types}}
    total = 0
    for row in rows:
        win_type = row["win_type"]
        if win_type not in by_type:
            continue
        n = row["n"] or 0
        by_type[win_type] += n
        side = row["winner_side"]
        if side in by_side:
            by_side[side][win_type] += n
        total += n
    return {"by_type": by_type, "by_side": by_side, "total": total}


def canonical_player_name(name):
    """A canonical key for a player name, to merge duplicate spellings.

    The detail tables sometimes carry the same player under variant names (for
    example "Moonlight" and "MOONLIGHT1"), which splits one player's sample into
    two lines and skews the player views. This casefolds, trims, and strips a
    trailing run of digits, but only when at least three characters remain, so a
    genuinely short or numeric handle is left alone rather than over-merged.
    Conservative on purpose: it is better to miss a merge than to fuse two real
    players.
    """
    if not name:
        return ""
    base = name.strip().casefold()
    stripped = re.sub(r"\d+$", "", base)
    return stripped if len(stripped) >= 3 else base


def merge_player_aliases(rows):
    """Rewrite per-player rows so variant spellings of one player agree.

    Groups the rows by canonical_player_name and rewrites each row's player_name
    to one display spelling per group (the most frequently seen original, ties
    broken by name), so the downstream aggregations treat the variants as one
    player. Rows with no player name pass through untouched. Returns new dicts and
    does not mutate the input, so a sqlite Row list is safe to pass.
    """
    counts = {}
    for r in rows:
        nm = r["player_name"]
        if not nm:
            continue
        key = canonical_player_name(nm)
        counts.setdefault(key, {})
        counts[key][nm] = counts[key].get(nm, 0) + 1
    display = {
        key: max(seen, key=lambda n: (seen[n], n))
        for key, seen in counts.items()
    }
    out = []
    for r in rows:
        d = dict(r)
        nm = d["player_name"]
        if nm:
            d["player_name"] = display[canonical_player_name(nm)]
        out.append(d)
    return out


def post_pistol_conversion(round_rows, team_name):
    """Win rate of the round right after a pistol, given the pistol was won or lost.

    Each row needs match_id, map_name, round_number, winner_team, and is_pistol.
    The rounds are grouped per map (by match and map name), and for each pistol
    round the immediately following round (round 2 after round 1, round 14 after
    round 13) is looked up by number. Two figures come back:

      - won the pistol, then won the next round: the conversion, does this team
        snowball the pistol into a 2-0 start,
      - lost the pistol, then won the next round anyway: the recovery, does it
        break the opponent's bonus round.

    This is a proxy for economy conversion, not the real thing: without buy types
    a forced-buy upset looks identical to a clean conversion, so it is labeled the
    "next round after a pistol", not eco conversion, and the two cases are kept
    separate rather than folded into one number. Reads the same scoped round set
    that pistol_winrate does, so no new data is needed.

    Returns {won_pistols, won_then_won, won_conv_rate, lost_pistols, lost_then_won,
    lost_recover_rate}; a rate is None when that case never came up.
    """
    games = {}
    for row in round_rows:
        rn = row["round_number"]
        if rn is None:
            continue
        key = (row["match_id"], row["map_name"])
        games.setdefault(key, {})[rn] = {
            "winner": row["winner_team"], "pistol": bool(row["is_pistol"]),
        }
    won_p = won_then = lost_p = lost_then = 0
    for rounds in games.values():
        for rn, info in rounds.items():
            if not info["pistol"]:
                continue
            nxt = rounds.get(rn + 1)
            if nxt is None:
                continue
            won_pistol = info["winner"] == team_name
            won_next = nxt["winner"] == team_name
            if won_pistol:
                won_p += 1
                won_then += 1 if won_next else 0
            else:
                lost_p += 1
                lost_then += 1 if won_next else 0
    return {
        "won_pistols": won_p, "won_then_won": won_then,
        "won_conv_rate": _rate(won_then, won_p),
        "lost_pistols": lost_p, "lost_then_won": lost_then,
        "lost_recover_rate": _rate(lost_then, lost_p),
    }


def margin_profile(map_rows, team_name):
    """How a team wins and loses maps by margin, not just whether it does.

    Each row needs map_name, team1_name, team2_name, team1_score, team2_score, and
    winner_name (the per-map score rows). The team's score and the opponent's are
    read from whichever slot the team is in; a map with a missing or tied score is
    skipped as undecided. From the decided maps:

      - close maps: decided by two rounds or fewer (a 13-11 or an overtime), with
        the win-loss record in them,
      - overtime maps: a map that reached overtime, taken as the losing side
        finishing on 12 or more (regulation ends at 13, so a 12+ loser means the
        map went past 12-12), with the record,
      - average winning margin and average losing margin: how decisively the team
        tends to win and lose.

    Two teams with the same map win rate can be opposite in character here, one
    winning 13-5 and losing on the wire, the other grinding everything out. These
    stay raw distribution splits and are never rolled into a "clutch" or
    "resilience" rating, which would be the banned composite.

    Returns a dict of those counts, records, and average margins; an average is
    None when the team has no map of that kind.
    """
    maps = close_played = close_won = ot_played = ot_won = 0
    win_margins, loss_margins = [], []
    for row in map_rows:
        if team_name == row["team1_name"]:
            us, them = row["team1_score"], row["team2_score"]
        elif team_name == row["team2_name"]:
            us, them = row["team2_score"], row["team1_score"]
        else:
            continue
        if us is None or them is None or us == them:
            continue
        maps += 1
        margin = us - them
        won = margin > 0
        if abs(margin) <= 2:
            close_played += 1
            close_won += 1 if won else 0
        if min(us, them) >= 12:
            ot_played += 1
            ot_won += 1 if won else 0
        if won:
            win_margins.append(margin)
        else:
            loss_margins.append(-margin)
    return {
        "maps": maps,
        "close_played": close_played, "close_won": close_won,
        "close_lost": close_played - close_won,
        "close_winrate": _rate(close_won, close_played),
        "ot_played": ot_played, "ot_won": ot_won,
        "ot_winrate": _rate(ot_won, ot_played),
        "avg_win_margin": (sum(win_margins) / len(win_margins)
                           if win_margins else None),
        "avg_loss_margin": (sum(loss_margins) / len(loss_margins)
                            if loss_margins else None),
    }


# The confidence levels the matchup log offers, low to high, used to order the
# calibration buckets so the readout walks from least to most confident.
CONFIDENCE_ORDER = ["very low", "low", "medium", "high", "very high"]


def calibration(entries):
    """How well the user's own confidence tracked the outcomes they later recorded.

    Each entry needs confidence (one of CONFIDENCE_ORDER), predicted_side ("a" or
    "b", the team the user leaned toward), and outcome_side ("a" or "b", who
    actually won). Only entries with both a prediction and a recorded outcome
    count, since calibration is prediction against result. Entries are grouped by
    confidence level: for each level, how many resolved and how many the user
    called correctly, with the hit rate.

    This scores the user's judgment, never a team, so it stays inside the charter:
    it tells the user how good their reads have been at each confidence level and
    never declares a matchup winner. Calibration is noisy over a handful of
    entries, so the caller should lean on the per-bucket counts and not read much
    into a rate over very few resolved calls.

    Returns {buckets: [{confidence, resolved, correct, rate}], resolved, correct,
    rate}, with buckets in CONFIDENCE_ORDER and only those that have a resolved
    entry included.
    """
    by_conf = {}
    total_resolved = total_correct = 0
    for e in entries:
        pred = e["predicted_side"] if "predicted_side" in e.keys() else None
        outcome = e["outcome_side"]
        if pred not in ("a", "b") or outcome not in ("a", "b"):
            continue
        conf = e["confidence"] or "medium"
        agg = by_conf.setdefault(conf, {"resolved": 0, "correct": 0})
        agg["resolved"] += 1
        agg["correct"] += 1 if pred == outcome else 0
        total_resolved += 1
        total_correct += 1 if pred == outcome else 0
    order = CONFIDENCE_ORDER + [c for c in by_conf if c not in CONFIDENCE_ORDER]
    buckets = [
        {"confidence": c, "resolved": by_conf[c]["resolved"],
         "correct": by_conf[c]["correct"],
         "rate": _rate(by_conf[c]["correct"], by_conf[c]["resolved"])}
        for c in order if c in by_conf
    ]
    return {
        "buckets": buckets, "resolved": total_resolved,
        "correct": total_correct, "rate": _rate(total_correct, total_resolved),
    }


def lineup_continuity(rows, five_names):
    """How many of a team's windowed maps the current five actually played.

    Each row is one player on one map and needs match_id, map_name, team_name, and
    player_name (the same per-map player rows the aggregates read). `five_names` is
    the casefolded current-five set. A map counts toward continuity when every
    player the team fielded on it is one of the current five (no stand-in or former
    player on the server), which is the concrete version of "how much of this
    aggregate belongs to the roster that will actually play".

    Team-level figures (side win rates, pistol, map win rate) cannot be reassigned
    to a roster the way the player figures can, so this quantifies how far to trust
    those aggregates as a read on the current lineup. Returns None when there is no
    current five to compare against (so the caller shows nothing rather than 0 of
    0), otherwise {maps_total, maps_current, pct}.
    """
    if not five_names:
        return None
    fielded = {}
    for row in rows:
        key = (row["match_id"], row["map_name"])
        name = (row["player_name"] or "").casefold()
        if name:
            fielded.setdefault(key, set()).add(name)
    maps_total = len(fielded)
    maps_current = sum(1 for names in fielded.values() if names <= five_names)
    return {"maps_total": maps_total, "maps_current": maps_current,
            "pct": _rate(maps_current, maps_total)}


def percentile(value, population):
    """Where a value sits among a population, as a 0..100 percentile rank.

    `population` is the field of comparable values (other teams' figures); None
    entries are dropped. Uses the midpoint convention (values strictly below, plus
    half of those equal), so a value in the middle of a symmetric field reads near
    50 rather than being biased up or down. Returns None when the value is missing
    or the field is empty, which is honest rather than a fabricated rank.

    This positions one statistic at a time, the same way the per-row A-versus-B
    gap does. It is never rolled up across statistics into an overall standing,
    which would be the ranking the charter forbids.
    """
    if value is None:
        return None
    nums = [v for v in population if v is not None]
    if not nums:
        return None
    below = sum(1 for v in nums if v < value)
    equal = sum(1 for v in nums if v == value)
    return 100.0 * (below + 0.5 * equal) / len(nums)


def field_summary(values):
    """Min, max, median, and mean of a field of values, skipping None.

    The baseline a single number is read against: a 52% pistol rate means little
    until you know the field sits near 50. Returns all-None counts when nothing is
    present. Descriptive only, never a ranking.
    """
    nums = sorted(v for v in values if v is not None)
    if not nums:
        return {"n": 0, "min": None, "max": None, "median": None, "mean": None}
    n = len(nums)
    mid = n // 2
    median = nums[mid] if n % 2 else (nums[mid - 1] + nums[mid]) / 2
    return {"n": n, "min": nums[0], "max": nums[-1], "median": median,
            "mean": sum(nums) / n}


# Opponent-strength tiers for the stat-by-tier split. Coarse on purpose: a finer
# split would spread an already thin cross-region sample too far. An opponent with
# no stored rank falls into "rest", since only ranked teams (mostly other
# franchises) carry a rank and the rest are weaker or unranked opposition.
TIER_ORDER = ["top10", "mid", "rest"]
TIER_LABELS = {"top10": "vs top 10", "mid": "vs 11-30", "rest": "vs 31+ / unranked"}


def tier_of_rank(rank):
    """Bucket an opponent regional rank into a strength tier (see TIER_ORDER)."""
    if rank is None:
        return "rest"
    if rank <= 10:
        return "top10"
    if rank <= 30:
        return "mid"
    return "rest"


def partition_by_tier(rows):
    """Split rows carrying an opp_rank into the opponent-strength tier buckets.

    Returns a dict keyed by tier (see TIER_ORDER) of the row subsets, so the
    caller can run the same aggregation (pistol, side, map win rate) over each and
    show them side by side. A row with no opp_rank falls into "rest" via
    tier_of_rank. This re-presents reachable data by opponent strength; it adds no
    new figure and makes no winner call.
    """
    buckets = {}
    for row in rows:
        buckets.setdefault(tier_of_rank(row["opp_rank"]), []).append(row)
    return buckets


def align_rosters(team_a_players, team_b_players):
    """Align two teams' players by inferred role for a head-to-head view.

    Each argument is a player_aggregates list. Every player is tagged with
    primary_role, grouped by role, and the two teams are paired position by
    position within each role (each side already sorted by maps played, so the
    most-used player in a role leads). When one team has more players in a role
    than the other, the shorter side pairs against None. Roles are walked in
    ROLE_ORDER, and a role where neither team has anyone is skipped.

    Returns an ordered list of {role, a, b}, where a and b are the matched player
    dicts (or None). The pairing is positional within a role, not a claim that
    the two players play the exact same position; it just lines up like for like
    so the user can compare comparable players.
    """
    def by_role(players):
        groups = {}
        for player in players:
            role = primary_role(player["agents"])
            groups.setdefault(role, []).append(player)
        return groups

    a_groups = by_role(team_a_players)
    b_groups = by_role(team_b_players)

    pairs = []
    for role in ROLE_ORDER:
        a_list = a_groups.get(role, [])
        b_list = b_groups.get(role, [])
        for i in range(max(len(a_list), len(b_list))):
            pairs.append({
                "role": role,
                "a": a_list[i] if i < len(a_list) else None,
                "b": b_list[i] if i < len(b_list) else None,
            })
    return pairs
