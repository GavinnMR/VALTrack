"""Pure derivations for VALTrack.

These take plain rows or lists and return computed figures with no database
access, so they are cheap to unit test against known inputs. The later
must-aggregate steps (side splits, pistol, opening duels) can add their pure
logic here too.
"""
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
