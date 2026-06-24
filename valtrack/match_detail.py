"""Parse and store the expensive per-match detail.

The cheap pass stored series-level match rows. This module takes
the per-match detail segment from vlrggapi and fills the rich tables that the
splits, per-map figures, and player statistics depend on: map_results,
map_player_stats, rounds, and match_vetos.

parse_match_detail is a pure function over the API segment so it can be unit
tested from a fixture with no database. store_match_detail writes the parsed
result and is idempotent: it clears a match's existing rich rows before
inserting, so re-running the detail pass never duplicates anything.

Economy is stored per map as VLR's aggregate buy-type table (a patched scraper
selects each map's own econ block instead of the first map's for every map).
Series-level performance (multikills, clutches, plants, defuses) is stored per
player per match, since VLR exposes those only for the whole series. Round
win-condition (elim, defuse, time, boom) is read from the round-square icon a
patched scraper now surfaces.
"""
from datetime import datetime, timezone

from valtrack.cleaning import (
    fix_encoding,
    is_pistol_round,
    parse_float,
    parse_int,
    parse_played_won,
    parse_vetos,
    side_to_phase,
)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _winner_name(team1_name, team2_name, team1_score, team2_score):
    """The name of the team with the higher score, or None when undecided."""
    if team1_score is None or team2_score is None:
        return None
    if team1_score > team2_score:
        return team1_name
    if team2_score > team1_score:
        return team2_name
    return None


def _parse_players(rows, team_name):
    """Parse one team's player rows for a map into stat dicts.

    player_id is left out here; it is resolved against the players table at store
    time, since matching by alias needs the database.
    """
    players = []
    for row in rows or []:
        name = fix_encoding(row.get("name"))
        if not name:
            continue
        # VLR reports first kills and deaths per side with the keys fk_t / fk_ct
        # (T = attack, CT = defense), so the _t value is the attack total and the
        # _ct value is the defense total, the same t/ct to atk/def convention the
        # rounds use.
        players.append(
            {
                "player_name": name,
                "team_name": team_name,
                "agent": fix_encoding(row.get("agent")) or None,
                "rating": parse_float(row.get("rating")),
                "acs": parse_float(row.get("acs")),
                "kills": parse_int(row.get("kills")),
                "deaths": parse_int(row.get("deaths")),
                "assists": parse_int(row.get("assists")),
                "kast": (row.get("kast") or "").strip() or None,
                "adr": parse_float(row.get("adr")),
                "hs_pct": (row.get("hs_pct") or "").strip() or None,
                "first_kills": parse_int(row.get("fk")),
                "first_deaths": parse_int(row.get("fd")),
                "first_kills_atk": parse_int(row.get("fk_t")),
                "first_kills_def": parse_int(row.get("fk_ct")),
                "first_deaths_atk": parse_int(row.get("fd_t")),
                "first_deaths_def": parse_int(row.get("fd_ct")),
            }
        )
    return players


_WIN_TYPES = frozenset({"elim", "defuse", "time", "boom"})


def _parse_rounds(rows, team1_name, team2_name):
    """Parse a map's round-by-round outcomes.

    Each API round gives the winning team slot (team1/team2), that team's side
    (t/ct), and (from the patched scraper) the win-condition icon. We translate
    the side to attack or defense, resolve the slot to a team name, and keep the
    win type when it is one of the four known conditions, else None.
    """
    rounds = []
    for row in rows or []:
        number = parse_int(row.get("round_num"))
        if number is None:
            continue
        winner = row.get("winner")
        # VLR renders a fixed 24-column round grid, so a map decided early (say
        # 13-2) leaves trailing empty columns with no winner. Those are not
        # rounds; skipping them keeps round counts honest for the side splits.
        if winner not in ("team1", "team2"):
            continue
        winner_team = team1_name if winner == "team1" else team2_name
        win_type = (row.get("win_type") or "").strip().lower()
        rounds.append(
            {
                "round_number": number,
                "winner_side": side_to_phase(row.get("side")),
                "winner_team": winner_team,
                "win_type": win_type if win_type in _WIN_TYPES else None,
                "is_pistol": 1 if is_pistol_round(number) else 0,
            }
        )
    return rounds


# VLR's economy table reports each buy type as "played (won)" in fixed columns
# (the headers are icons, so the parser yields positional string keys): "2" eco
# (full save), "3" light buy ($), "4" half buy ($$), "5" full buy ($$$). Pistols
# (column "1", won only) are left to the rounds table. Column "0" is the team tag.
_ECON_BUY_TYPES = [("2", "eco"), ("3", "light"), ("4", "half"), ("5", "full")]


def _parse_map_economy(econ_rows, tag_to_name):
    """Parse a map's aggregate economy table into per-team buy-type rows.

    Each input row is one team's buy-type table for the map, keyed positionally.
    The leading cell is the team tag, which is resolved to the full team name so
    it matches the rest of the stored data. A buy type with no played count is
    skipped rather than stored as zero of zero.
    """
    out = []
    for row in econ_rows or []:
        tag = fix_encoding((row.get("0") or "").strip())
        if not tag:
            continue
        team_name = tag_to_name.get(tag.casefold(), tag)
        for key, buy in _ECON_BUY_TYPES:
            played, won = parse_played_won(row.get(key))
            if played is None:
                continue
            out.append({"team_name": team_name, "buy_type": buy,
                        "played": played, "won": won})
    return out


# The performance tab's advanced-stats columns, by header label with a positional
# fallback (the live table has no header row, so the parser yields numeric string
# keys). Multikills are rounds with exactly N kills; the 1vX columns are clutches
# won at that depth; PL and DE are plants and defuses. The Econ rating column is
# intentionally dropped (it is a rating, not a count).
_ADV_COLUMNS = {
    "mk_2k": ("2K", 2), "mk_3k": ("3K", 3), "mk_4k": ("4K", 4), "mk_5k": ("5K", 5),
    "clutch_1v1": ("1v1", 6), "clutch_1v2": ("1v2", 7), "clutch_1v3": ("1v3", 8),
    "clutch_1v4": ("1v4", 9), "clutch_1v5": ("1v5", 10),
    "plants": ("PL", 12), "defuses": ("DE", 13),
}


def _adv_value(row, label, index):
    """Read one advanced-stat cell by header label, falling back to its column index.

    A blank cell means the player did none of that thing, so it reads as 0 rather
    than None: these are counts, and a missing count is genuinely zero here.
    """
    val = row.get(label)
    if val in (None, ""):
        val = row.get(str(index))
    return parse_int(val) or 0


def _parse_performance(perf_rows, player_team):
    """Parse the series-level performance rows into per-player counts.

    VLR exposes multikills, clutches, plants, and defuses only for the whole
    series, so this is one entry per player for the match. The team is resolved
    from who appeared on each map (the performance row carries only the name).
    """
    out = []
    for row in perf_rows or []:
        name = fix_encoding(row.get("player"))
        if not name:
            continue
        entry = {"player_name": name, "team_name": player_team.get(name)}
        for field, (label, index) in _ADV_COLUMNS.items():
            entry[field] = _adv_value(row, label, index)
        out.append(entry)
    return out


def _parse_map(map_obj, map_order, team1_name, team2_name, tag_to_name=None):
    """Parse a single map block into a result row with its players and rounds."""
    score = map_obj.get("score") or {}
    score_ct = map_obj.get("score_ct") or {}
    score_t = map_obj.get("score_t") or {}

    team1_score = parse_int(score.get("team1"))
    team2_score = parse_int(score.get("team2"))

    players = map_obj.get("players") or {}
    rounds = _parse_rounds(map_obj.get("rounds"), team1_name, team2_name)

    # Which team picked this map, from the patched scraper's team marker. The
    # decider (left after both picks) has no marker, so picked_by_name stays None.
    picked = map_obj.get("picked_by_team")
    if picked == "team1":
        picked_by_name = team1_name
    elif picked == "team2":
        picked_by_name = team2_name
    else:
        picked_by_name = None

    return {
        "map_name": fix_encoding(map_obj.get("map_name")) or None,
        "map_order": map_order,
        "team1_name": team1_name,
        "team2_name": team2_name,
        "team1_score": team1_score,
        "team2_score": team2_score,
        # VLR reports each team's CT (defense) and T (attack) round totals. The
        # authoritative side splits come from the rounds table; these columns are
        # a convenient snapshot of VLR's half totals.
        "team1_atk_rounds": parse_int(score_t.get("team1")),
        "team1_def_rounds": parse_int(score_ct.get("team1")),
        "team2_atk_rounds": parse_int(score_t.get("team2")),
        "team2_def_rounds": parse_int(score_ct.get("team2")),
        "winner_name": _winner_name(team1_name, team2_name, team1_score, team2_score),
        "picked_by_name": picked_by_name,
        "economy": _parse_map_economy(map_obj.get("economy"), tag_to_name or {}),
        "players": (
            _parse_players(players.get("team1"), team1_name)
            + _parse_players(players.get("team2"), team2_name)
        ),
        "rounds": rounds,
    }


def parse_match_detail(segment):
    """Turn a vlrggapi match detail segment into structured rows.

    Returns a dict with event_name, map_vetos_raw, a parsed vetos list, and a
    maps list (each with its result fields, players, and rounds). Pure: no
    database access, so it can be tested from a fixture.
    """
    event = segment.get("event") or {}
    teams = segment.get("teams") or []
    team1_name = fix_encoding(teams[0].get("name")) if len(teams) > 0 else None
    team2_name = fix_encoding(teams[1].get("name")) if len(teams) > 1 else None

    # Tag -> full name, so the economy table (which keys teams by tag) can be
    # stored under the same names as everything else.
    tag_to_name = {}
    for team in teams:
        tag = fix_encoding((team.get("tag") or "").strip())
        name = fix_encoding(team.get("name"))
        if tag and name:
            tag_to_name[tag.casefold()] = name

    maps = []
    for index, map_obj in enumerate(segment.get("maps") or [], start=1):
        maps.append(_parse_map(map_obj, index, team1_name, team2_name, tag_to_name))

    # Player -> team name from everyone who appeared on any map, so the
    # series-level performance rows (which carry only the player name) can be
    # attributed to a team.
    player_team = {}
    for game in maps:
        for player in game["players"]:
            if player["player_name"] and player["team_name"]:
                player_team.setdefault(player["player_name"], player["team_name"])

    performance = _parse_performance(
        (segment.get("performance") or {}).get("advanced_stats"), player_team
    )

    map_vetos_raw = segment.get("map_vetos") or None
    return {
        "event_name": fix_encoding(event.get("name")) or None,
        "map_vetos_raw": map_vetos_raw,
        "vetos": parse_vetos(map_vetos_raw),
        "maps": maps,
        "performance": performance,
    }


def _resolve_player_id(conn, alias):
    """Best-effort player id for an alias, matched against the players table.

    Returns None when no roster player matches, which is honest rather than
    inventing an id. Detail rows carry only the alias, not the VLR player id.
    """
    if not alias:
        return None
    row = conn.execute(
        "SELECT id FROM players WHERE alias = ? COLLATE NOCASE", (alias,)
    ).fetchone()
    return row["id"] if row else None


def store_match_detail(conn, match_id, parsed):
    """Write a parsed match detail to the rich tables. Idempotent.

    Clears this match's existing map_results, map_player_stats, rounds,
    match_vetos, map_economy, and match_player_perf before inserting, so a re-run
    never duplicates rows. Sets matches.details_fetched_at so the detail pass
    skips this match next time, even when it has no maps (a forfeit or unplayed
    match is still "done").
    """
    now = _now()

    conn.execute("DELETE FROM map_results WHERE match_id = ?", (match_id,))
    conn.execute("DELETE FROM map_player_stats WHERE match_id = ?", (match_id,))
    conn.execute("DELETE FROM rounds WHERE match_id = ?", (match_id,))
    conn.execute("DELETE FROM match_vetos WHERE match_id = ?", (match_id,))
    conn.execute("DELETE FROM map_economy WHERE match_id = ?", (match_id,))
    conn.execute("DELETE FROM match_player_perf WHERE match_id = ?", (match_id,))

    # The economy table keys teams by tag (PRX, LEV) and the match segment often
    # carries no tag, so the parse may leave the raw tag as the team name. Resolve
    # it to the full name via the teams table, scoped to the two teams in this
    # match so a tag never lands on an unrelated org.
    tag_resolver = {}
    for game in parsed["maps"]:
        for name in (game["team1_name"], game["team2_name"]):
            if not name or name in tag_resolver.values():
                continue
            row = conn.execute(
                "SELECT tag FROM teams WHERE name = ? COLLATE NOCASE", (name,)
            ).fetchone()
            if row and row["tag"]:
                tag_resolver[row["tag"].casefold()] = name

    for game in parsed["maps"]:
        conn.execute(
            """
            INSERT INTO map_results (
                match_id, map_name, map_order, team1_name, team2_name,
                team1_score, team2_score, team1_atk_rounds, team1_def_rounds,
                team2_atk_rounds, team2_def_rounds, winner_name, picked_by_name,
                fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                match_id, game["map_name"], game["map_order"],
                game["team1_name"], game["team2_name"],
                game["team1_score"], game["team2_score"],
                game["team1_atk_rounds"], game["team1_def_rounds"],
                game["team2_atk_rounds"], game["team2_def_rounds"],
                game["winner_name"], game.get("picked_by_name"), now,
            ),
        )
        for econ in game.get("economy") or []:
            team_name = tag_resolver.get(
                (econ["team_name"] or "").casefold(), econ["team_name"])
            conn.execute(
                """
                INSERT INTO map_economy (
                    match_id, map_name, team_name, buy_type, played, won, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    match_id, game["map_name"], team_name,
                    econ["buy_type"], econ["played"], econ["won"], now,
                ),
            )
        for player in game["players"]:
            conn.execute(
                """
                INSERT INTO map_player_stats (
                    match_id, map_name, player_id, player_name, team_name, agent,
                    rating, acs, kills, deaths, assists, kast, adr, hs_pct,
                    first_kills, first_deaths, first_kills_atk, first_kills_def,
                    first_deaths_atk, first_deaths_def, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    match_id, game["map_name"],
                    _resolve_player_id(conn, player["player_name"]),
                    player["player_name"], player["team_name"], player["agent"],
                    player["rating"], player["acs"], player["kills"],
                    player["deaths"], player["assists"], player["kast"],
                    player["adr"], player["hs_pct"], player["first_kills"],
                    player["first_deaths"], player["first_kills_atk"],
                    player["first_kills_def"], player["first_deaths_atk"],
                    player["first_deaths_def"], now,
                ),
            )
        for rnd in game["rounds"]:
            conn.execute(
                """
                INSERT INTO rounds (
                    match_id, map_name, round_number, winner_side, winner_team,
                    win_type, is_pistol, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    match_id, game["map_name"], rnd["round_number"],
                    rnd["winner_side"], rnd["winner_team"], rnd["win_type"],
                    rnd["is_pistol"], now,
                ),
            )

    for perf in parsed.get("performance") or []:
        conn.execute(
            """
            INSERT INTO match_player_perf (
                match_id, player_id, player_name, team_name,
                mk_2k, mk_3k, mk_4k, mk_5k,
                clutch_1v1, clutch_1v2, clutch_1v3, clutch_1v4, clutch_1v5,
                plants, defuses, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                match_id, _resolve_player_id(conn, perf["player_name"]),
                perf["player_name"], perf["team_name"],
                perf["mk_2k"], perf["mk_3k"], perf["mk_4k"], perf["mk_5k"],
                perf["clutch_1v1"], perf["clutch_1v2"], perf["clutch_1v3"],
                perf["clutch_1v4"], perf["clutch_1v5"],
                perf["plants"], perf["defuses"], now,
            ),
        )

    for veto in parsed["vetos"]:
        conn.execute(
            """
            INSERT INTO match_vetos (
                match_id, seq, team_token, action, map_name, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                match_id, veto["seq"], veto["team_token"], veto["action"],
                veto["map_name"], now,
            ),
        )

    conn.execute(
        """
        UPDATE matches SET
            event_name = COALESCE(?, event_name),
            map_vetos_raw = ?,
            details_fetched_at = ?
        WHERE match_id = ?
        """,
        (parsed["event_name"], parsed["map_vetos_raw"], now, match_id),
    )
