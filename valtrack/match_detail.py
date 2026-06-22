"""Parse and store the expensive per-match detail.

The cheap pass in Build Step 2 stored series-level match rows. This module takes
the per-match detail segment from vlrggapi and fills the rich tables that the
splits, per-map figures, and player statistics depend on: map_results,
map_player_stats, rounds, and match_vetos.

parse_match_detail is a pure function over the API segment so it can be unit
tested from a fixture with no database. store_match_detail writes the parsed
result and is idempotent: it clears a match's existing rich rows before
inserting, so re-running the detail pass never duplicates anything.

Economy is deliberately not stored here. vlrggapi's match detail returns only
the first map's economy table for every map, so per-map economy is not reliably
available yet. Pistol-round win rate is recovered from the rounds table instead.
"""
from datetime import datetime, timezone

from valtrack.cleaning import (
    fix_encoding,
    is_pistol_round,
    parse_float,
    parse_int,
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
            }
        )
    return players


def _parse_rounds(rows, team1_name, team2_name):
    """Parse a map's round-by-round outcomes.

    Each API round gives the winning team slot (team1/team2) and that team's
    side (t/ct). We translate the side to attack or defense and resolve the slot
    to a team name. win_type is not provided by the source, so it stays None.
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
        rounds.append(
            {
                "round_number": number,
                "winner_side": side_to_phase(row.get("side")),
                "winner_team": winner_team,
                "win_type": None,
                "is_pistol": 1 if is_pistol_round(number) else 0,
            }
        )
    return rounds


def _parse_map(map_obj, map_order, team1_name, team2_name):
    """Parse a single map block into a result row with its players and rounds."""
    score = map_obj.get("score") or {}
    score_ct = map_obj.get("score_ct") or {}
    score_t = map_obj.get("score_t") or {}

    team1_score = parse_int(score.get("team1"))
    team2_score = parse_int(score.get("team2"))

    players = map_obj.get("players") or {}
    rounds = _parse_rounds(map_obj.get("rounds"), team1_name, team2_name)

    return {
        "map_name": fix_encoding(map_obj.get("map_name")) or None,
        "map_order": map_order,
        "team1_name": team1_name,
        "team2_name": team2_name,
        "team1_score": team1_score,
        "team2_score": team2_score,
        # VLR reports each team's CT (defense) and T (attack) round totals. The
        # authoritative side splits in Build Step 6 come from the rounds table;
        # these columns are a convenient snapshot of VLR's half totals.
        "team1_atk_rounds": parse_int(score_t.get("team1")),
        "team1_def_rounds": parse_int(score_ct.get("team1")),
        "team2_atk_rounds": parse_int(score_t.get("team2")),
        "team2_def_rounds": parse_int(score_ct.get("team2")),
        "winner_name": _winner_name(team1_name, team2_name, team1_score, team2_score),
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

    maps = []
    for index, map_obj in enumerate(segment.get("maps") or [], start=1):
        maps.append(_parse_map(map_obj, index, team1_name, team2_name))

    map_vetos_raw = segment.get("map_vetos") or None
    return {
        "event_name": fix_encoding(event.get("name")) or None,
        "map_vetos_raw": map_vetos_raw,
        "vetos": parse_vetos(map_vetos_raw),
        "maps": maps,
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

    Clears this match's existing map_results, map_player_stats, rounds, and
    match_vetos before inserting, so a re-run never duplicates rows. Sets
    matches.details_fetched_at so the detail pass skips this match next time,
    even when it has no maps (a forfeit or unplayed match is still "done").
    """
    now = _now()

    conn.execute("DELETE FROM map_results WHERE match_id = ?", (match_id,))
    conn.execute("DELETE FROM map_player_stats WHERE match_id = ?", (match_id,))
    conn.execute("DELETE FROM rounds WHERE match_id = ?", (match_id,))
    conn.execute("DELETE FROM match_vetos WHERE match_id = ?", (match_id,))

    for game in parsed["maps"]:
        conn.execute(
            """
            INSERT INTO map_results (
                match_id, map_name, map_order, team1_name, team2_name,
                team1_score, team2_score, team1_atk_rounds, team1_def_rounds,
                team2_atk_rounds, team2_def_rounds, winner_name, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                match_id, game["map_name"], game["map_order"],
                game["team1_name"], game["team2_name"],
                game["team1_score"], game["team2_score"],
                game["team1_atk_rounds"], game["team1_def_rounds"],
                game["team2_atk_rounds"], game["team2_def_rounds"],
                game["winner_name"], now,
            ),
        )
        for player in game["players"]:
            conn.execute(
                """
                INSERT INTO map_player_stats (
                    match_id, map_name, player_id, player_name, team_name, agent,
                    rating, acs, kills, deaths, assists, kast, adr, hs_pct,
                    first_kills, first_deaths, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    match_id, game["map_name"],
                    _resolve_player_id(conn, player["player_name"]),
                    player["player_name"], player["team_name"], player["agent"],
                    player["rating"], player["acs"], player["kills"],
                    player["deaths"], player["assists"], player["kast"],
                    player["adr"], player["hs_pct"], player["first_kills"],
                    player["first_deaths"], now,
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
