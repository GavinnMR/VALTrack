"""Read-side queries for the VALTrack app.

The Streamlit app is a presentation layer and holds no SQL of its own. These
helpers read the stored teams, rosters, and matches and hand back plain rows the
UI can render. Keeping the queries here also lets the few computed figures, such
as a team's overall record, be unit tested without standing up the app.

Every function takes an open connection, matching the ingestion side, so a test
can pass a temporary database. Match-derived figures take an optional date
window (see valtrack.window); the default is all time, so callers that do not
care about a range can leave it out.
"""
from valtrack.window import DateWindow


def list_teams(conn):
    """Return franchise teams for the dropdowns, ordered by league then rank.

    Teams with no regional rank (inactive orgs that sit on no ladder) sort last
    within their league but are still listed, so the user can always pick them.
    """
    return conn.execute(
        """
        SELECT id, name, tag, league, region, regional_rank
        FROM teams
        ORDER BY league,
                 CASE WHEN regional_rank IS NULL THEN 1 ELSE 0 END,
                 regional_rank,
                 name COLLATE NOCASE
        """
    ).fetchall()


def get_team(conn, team_id):
    """Return one team's identity, ranking, and snapshot fields, or None.

    The earnings and rating fields are VLR's current all-time values, not
    windowed figures, so the UI labels them as a snapshot.
    """
    return conn.execute(
        """
        SELECT id, name, tag, logo, league, region,
               regional_rank, world_rank, rating, peak_rating,
               streak, record, earnings, total_winnings, last_played
        FROM teams
        WHERE id = ?
        """,
        (team_id,),
    ).fetchone()


def get_roster(conn, team_id):
    """Return a team's stored roster joined to player identities.

    Captain first, then alias order. The split into the current five, stand-ins,
    and staff happens in valtrack.stats, because the stored is_staff flag is
    unreliable and roles have to be read from the role text.
    """
    return conn.execute(
        """
        SELECT p.id, p.alias, p.real_name, p.country,
               r.role, r.is_captain, r.is_staff
        FROM rosters r
        JOIN players p ON p.id = r.player_id
        WHERE r.team_id = ?
        ORDER BY r.is_captain DESC, p.alias COLLATE NOCASE
        """,
        (team_id,),
    ).fetchall()


def team_record(conn, team_id, window=None):
    """Compute a team's series record over the window across stored matches.

    A franchise team can sit in either the team1 or team2 slot of a match,
    because the same match is pulled from both teams' histories and the last
    write wins the team1 slot. So we look in both slots and compare that side's
    score against the opponent's. A match counts as decided only when both scores
    are present and unequal, so ties and unplayed rows drop out. The window
    filters on the match date; the default all-time window applies no filter.

    Returns a dict with wins, losses, and decided (wins + losses).
    """
    window = window or DateWindow.all_time()
    wclause, wparams = window.clause("date")
    row = conn.execute(
        f"""
        SELECT
            SUM(CASE
                WHEN team1_id = ? AND team1_score > team2_score THEN 1
                WHEN team2_id = ? AND team2_score > team1_score THEN 1
                ELSE 0 END) AS wins,
            SUM(CASE
                WHEN team1_id = ? AND team1_score < team2_score THEN 1
                WHEN team2_id = ? AND team2_score < team1_score THEN 1
                ELSE 0 END) AS losses
        FROM matches
        WHERE (team1_id = ? OR team2_id = ?)
          AND team1_score IS NOT NULL
          AND team2_score IS NOT NULL
          AND {wclause}
        """,
        [team_id, team_id, team_id, team_id, team_id, team_id, *wparams],
    ).fetchone()
    wins = row["wins"] or 0
    losses = row["losses"] or 0
    return {"wins": wins, "losses": losses, "decided": wins + losses}


def decided_results(conn, team_id, window=None):
    """Return the team's decided results within the window, newest first.

    Each item is "W" or "L" from the team's point of view. Ties and undecided
    matches are excluded, since form and a streak only mean something over games
    with a winner. The full list comes back so a current streak of any length is
    counted correctly; the caller trims it for the form display.
    """
    window = window or DateWindow.all_time()
    wclause, wparams = window.clause("date")
    rows = conn.execute(
        f"""
        SELECT CASE
                   WHEN team1_id = ?
                   THEN (CASE WHEN team1_score > team2_score THEN 'W' ELSE 'L' END)
                   ELSE (CASE WHEN team2_score > team1_score THEN 'W' ELSE 'L' END)
               END AS result
        FROM matches
        WHERE (team1_id = ? OR team2_id = ?)
          AND team1_score IS NOT NULL
          AND team2_score IS NOT NULL
          AND team1_score != team2_score
          AND {wclause}
        ORDER BY date DESC, match_id DESC
        """,
        [team_id, team_id, team_id, *wparams],
    ).fetchall()
    return [r["result"] for r in rows]


def recent_matches(conn, team_id, window=None, limit=10):
    """Return a team's recent matches within the window, newest first.

    Each row is framed from this team's point of view: opponent name and tag,
    the score as team then opponent, the result, the date, and the round label.
    The team can be in either slot, so the opponent comes from the other slot.
    Only decided matches carry a result; an undecided one returns None for it.
    """
    window = window or DateWindow.all_time()
    wclause, wparams = window.clause("date")
    rows = conn.execute(
        f"""
        SELECT match_id, date, event_round,
               team1_id, team1_name, team1_tag, team1_score,
               team2_id, team2_name, team2_tag, team2_score
        FROM matches
        WHERE (team1_id = ? OR team2_id = ?)
          AND {wclause}
        ORDER BY date DESC, match_id DESC
        LIMIT ?
        """,
        [team_id, team_id, *wparams, limit],
    ).fetchall()
    out = []
    for r in rows:
        if r["team1_id"] == team_id:
            opp_name, opp_tag = r["team2_name"], r["team2_tag"]
            us, them = r["team1_score"], r["team2_score"]
        else:
            opp_name, opp_tag = r["team1_name"], r["team1_tag"]
            us, them = r["team2_score"], r["team1_score"]
        result = None
        if us is not None and them is not None and us != them:
            result = "W" if us > them else "L"
        out.append({
            "match_id": r["match_id"],
            "date": r["date"],
            "round": r["event_round"],
            "opponent": opp_name,
            "opponent_tag": opp_tag,
            "score": (us, them),
            "result": result,
        })
    return out


def _team_name(conn, team_id):
    """The team's stored name, used to match the detail-page team naming.

    The per-match detail tables (map_results, rounds) record teams by name, not
    by our team id, and those names line up with teams.name. So the side-split
    queries resolve the id to a name once and filter the detail rows on it.
    """
    row = conn.execute("SELECT name FROM teams WHERE id = ?", (team_id,)).fetchone()
    return row["name"] if row else None


def team_map_results(conn, team_id, window=None):
    """Return the per-map results for maps this team played, within the window.

    One row per map instance, framed by the detail-page team naming: map_name,
    winner_name, and the two side names. The date filter is on the parent match.
    Feeds stats.map_winrates. Returns [] when the team has no stored detail.
    """
    name = _team_name(conn, team_id)
    if name is None:
        return []
    window = window or DateWindow.all_time()
    wclause, wparams = window.clause("m.date")
    return conn.execute(
        f"""
        SELECT mr.match_id, mr.map_name, mr.winner_name,
               mr.team1_name, mr.team2_name
        FROM map_results mr
        JOIN matches m ON m.match_id = mr.match_id
        WHERE (mr.team1_name = ? OR mr.team2_name = ?)
          AND {wclause}
        """,
        [name, name, *wparams],
    ).fetchall()


def team_rounds(conn, team_id, window=None):
    """Return the round-level rows for maps this team played, within the window.

    One row per decided round on a map the team was in: map_name, winner_side,
    winner_team, and is_pistol. Scoped to the team's maps by joining map_results
    on the match and map, so the round set belongs only to maps this team played.
    The date filter is on the parent match. Feeds stats.side_winrates and, via the
    is_pistol flag, stats.pistol_winrate, so both read the same round set.
    """
    name = _team_name(conn, team_id)
    if name is None:
        return []
    window = window or DateWindow.all_time()
    wclause, wparams = window.clause("m.date")
    return conn.execute(
        f"""
        SELECT r.map_name, r.winner_side, r.winner_team, r.is_pistol
        FROM rounds r
        JOIN map_results mr
          ON mr.match_id = r.match_id AND mr.map_name = r.map_name
        JOIN matches m ON m.match_id = r.match_id
        WHERE (mr.team1_name = ? OR mr.team2_name = ?)
          AND {wclause}
        """,
        [name, name, *wparams],
    ).fetchall()


def team_player_opening(conn, team_id, window=None):
    """Return per-map opening-duel counts for this team's players in the window.

    One row per player per map the team played, framed by the detail-page team
    naming: player_name, team_name, the combined first_kills / first_deaths, and
    the per-side first_kills_atk / first_kills_def / first_deaths_atk /
    first_deaths_def. The date filter is on the parent match. Feeds
    stats.opening_duels. Returns [] when the team has no stored detail.
    """
    name = _team_name(conn, team_id)
    if name is None:
        return []
    window = window or DateWindow.all_time()
    wclause, wparams = window.clause("m.date")
    return conn.execute(
        f"""
        SELECT mps.player_name, mps.team_name,
               mps.first_kills, mps.first_deaths,
               mps.first_kills_atk, mps.first_kills_def,
               mps.first_deaths_atk, mps.first_deaths_def
        FROM map_player_stats mps
        JOIN matches m ON m.match_id = mps.match_id
        WHERE mps.team_name = ?
          AND {wclause}
        """,
        [name, *wparams],
    ).fetchall()


def team_player_stats(conn, team_id, window=None):
    """Return per-player per-map stat lines for this team within the window.

    One row per player per map the team played, framed by the detail-page team
    naming: player_name, player_id, agent, the per-map rating / acs / kills /
    deaths / assists / kast / adr / hs_pct, the first_kills / first_deaths, and
    the map's total rounds (both teams' scores summed) used to round-weight the
    rate stats and to turn kills and assists into per-round figures. The round
    count comes from map_results and is NULL when no result row is stored, in
    which case the aggregation skips that map's round-weighted contribution. The
    date filter is on the parent match. Feeds stats.player_aggregates. Returns []
    when the team has no stored detail.
    """
    name = _team_name(conn, team_id)
    if name is None:
        return []
    window = window or DateWindow.all_time()
    wclause, wparams = window.clause("m.date")
    return conn.execute(
        f"""
        SELECT mps.player_name, mps.player_id, mps.team_name, mps.map_name,
               mps.agent,
               mps.rating, mps.acs, mps.kills, mps.deaths, mps.assists,
               mps.kast, mps.adr, mps.hs_pct,
               mps.first_kills, mps.first_deaths,
               (mr.team1_score + mr.team2_score) AS map_rounds
        FROM map_player_stats mps
        JOIN matches m ON m.match_id = mps.match_id
        LEFT JOIN map_results mr
          ON mr.match_id = mps.match_id AND mr.map_name = mps.map_name
        WHERE mps.team_name = ?
          AND {wclause}
        """,
        [name, *wparams],
    ).fetchall()


def match_date_bounds(conn):
    """Return (min_date, max_date) ISO strings across stored matches.

    Used to bound the date picker. Returns (None, None) when no dated matches
    are stored.
    """
    row = conn.execute(
        "SELECT MIN(date) AS mn, MAX(date) AS mx "
        "FROM matches WHERE date IS NOT NULL"
    ).fetchone()
    return (row["mn"], row["mx"])
