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
from datetime import date, timedelta

from valtrack.window import DateWindow, EventFilter, StageFilter


def _scope(window, events, stage, prefix=""):
    """Combined date-window, event, and stage WHERE fragment for the matches table.

    Every match-derived read shares the same three optional filters, so building
    their clause in one place keeps them consistent and the param order trivially
    correct: the fragment is fully ANDed and the params come back in order, ready
    to append after a query's team predicate. `prefix` is "" for a query reading
    the matches table directly and "m." for a detail query joining it as m. Pass
    events=None for the detail reads, where LAN/online stays a match-level filter
    and only the date and stage apply; a None filter contributes the always-true
    "1=1" so the caller never has to branch.
    """
    window = window or DateWindow.all_time()
    events = events or EventFilter()
    stage = stage or StageFilter()
    wc, wp = window.clause(prefix + "date")
    ec, ep = events.clause(prefix + "event_name")
    sc, sp = stage.clause(prefix + "match_stage")
    return f"({wc}) AND ({ec}) AND ({sc})", [*wp, *ep, *sp]


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


def team_record(conn, team_id, window=None, events=None, stage=None):
    """Compute a team's series record over the window across stored matches.

    A franchise team can sit in either the team1 or team2 slot of a match,
    because the same match is pulled from both teams' histories and the last
    write wins the team1 slot. So we look in both slots and compare that side's
    score against the opponent's. A match counts as decided only when both scores
    are present and unequal, so ties and unplayed rows drop out. The window
    filters on the match date; the optional event filter narrows to LAN or online
    events, and the optional stage filter to group or playoff play. The defaults
    apply no filter.

    Returns a dict with wins, losses, and decided (wins + losses).
    """
    scope, sparams = _scope(window, events, stage)
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
          AND {scope}
        """,
        [team_id, team_id, team_id, team_id, team_id, team_id, *sparams],
    ).fetchone()
    wins = row["wins"] or 0
    losses = row["losses"] or 0
    return {"wins": wins, "losses": losses, "decided": wins + losses}


def decided_results(conn, team_id, window=None, events=None, stage=None):
    """Return the team's decided results within the window, newest first.

    Each item is "W" or "L" from the team's point of view. Ties and undecided
    matches are excluded, since form and a streak only mean something over games
    with a winner. The optional event and stage filters narrow to LAN or online
    and to group or playoff play. The full list comes back so a current streak of
    any length is counted correctly; the caller trims it for the form display.
    """
    scope, sparams = _scope(window, events, stage)
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
          AND {scope}
        ORDER BY date DESC, match_id DESC
        """,
        [team_id, team_id, team_id, *sparams],
    ).fetchall()
    return [r["result"] for r in rows]


def recent_matches(conn, team_id, window=None, limit=10, events=None, stage=None):
    """Return a team's recent matches within the window, newest first.

    Each row is framed from this team's point of view: opponent name and tag,
    the score as team then opponent, the result, the date, and the round label.
    The team can be in either slot, so the opponent comes from the other slot.
    Only decided matches carry a result; an undecided one returns None for it.
    The optional event and stage filters narrow to LAN or online and to group or
    playoff play.
    """
    scope, sparams = _scope(window, events, stage)
    rows = conn.execute(
        f"""
        SELECT match_id, date, event_round,
               team1_id, team1_name, team1_tag, team1_score,
               team2_id, team2_name, team2_tag, team2_score
        FROM matches
        WHERE (team1_id = ? OR team2_id = ?)
          AND {scope}
        ORDER BY date DESC, match_id DESC
        LIMIT ?
        """,
        [team_id, team_id, *sparams, limit],
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


def team_map_results(conn, team_id, window=None, stage=None):
    """Return the per-map results for maps this team played, within the window.

    One row per map instance, framed by the detail-page team naming: map_name,
    winner_name, the two side names, the two side scores (so the margin profile can
    read how close each map was), match_date (so the veto view can show how recent
    each map's win-rate sample is), and opp_rank, the opponent's current regional
    rank (for the by-tier split; None when the opponent has no stored rank). The
    date filter is on the parent match, and the optional stage filter narrows to
    group or playoff play. Feeds stats.map_winrates and stats.margin_profile.
    Returns [] when the team has no stored detail.
    """
    name = _team_name(conn, team_id)
    if name is None:
        return []
    scope, sparams = _scope(window, None, stage, "m.")
    return conn.execute(
        f"""
        SELECT mr.match_id, mr.map_name, mr.winner_name,
               mr.team1_name, mr.team2_name,
               mr.team1_score, mr.team2_score,
               m.date AS match_date,
               t.regional_rank AS opp_rank
        FROM map_results mr
        JOIN matches m ON m.match_id = mr.match_id
        LEFT JOIN teams t ON t.id = (
            CASE WHEN m.team1_id = ? THEN m.team2_id ELSE m.team1_id END
        )
        WHERE (mr.team1_name = ? OR mr.team2_name = ?)
          AND {scope}
        """,
        [team_id, name, name, *sparams],
    ).fetchall()


def team_rounds(conn, team_id, window=None, stage=None):
    """Return the round-level rows for maps this team played, within the window.

    One row per decided round on a map the team was in: match_id, map_name,
    round_number, winner_side, winner_team, is_pistol, and opp_rank (the
    opponent's current regional rank, None when unranked, for the by-tier split).
    Scoped to the team's maps by joining map_results on the match and map, so the
    round set belongs only to maps this team played. The date filter is on the
    parent match and the optional stage filter narrows to group or playoff play.
    Feeds stats.side_winrates, stats.pistol_winrate (via is_pistol), and
    stats.post_pistol_conversion (via round_number), so they share one round set.
    """
    name = _team_name(conn, team_id)
    if name is None:
        return []
    scope, sparams = _scope(window, None, stage, "m.")
    return conn.execute(
        f"""
        SELECT r.match_id, r.map_name, r.round_number,
               r.winner_side, r.winner_team, r.is_pistol,
               t.regional_rank AS opp_rank
        FROM rounds r
        JOIN map_results mr
          ON mr.match_id = r.match_id AND mr.map_name = r.map_name
        JOIN matches m ON m.match_id = r.match_id
        LEFT JOIN teams t ON t.id = (
            CASE WHEN m.team1_id = ? THEN m.team2_id ELSE m.team1_id END
        )
        WHERE (mr.team1_name = ? OR mr.team2_name = ?)
          AND {scope}
        """,
        [team_id, name, name, *sparams],
    ).fetchall()


def team_player_opening(conn, team_id, window=None, stage=None):
    """Return per-map opening-duel counts for this team's players in the window.

    One row per player per map the team played, framed by the detail-page team
    naming: player_name, team_name, the combined first_kills / first_deaths, and
    the per-side first_kills_atk / first_kills_def / first_deaths_atk /
    first_deaths_def. The date filter is on the parent match and the optional stage
    filter narrows to group or playoff play. Feeds stats.opening_duels. Returns []
    when the team has no stored detail.
    """
    name = _team_name(conn, team_id)
    if name is None:
        return []
    scope, sparams = _scope(window, None, stage, "m.")
    return conn.execute(
        f"""
        SELECT mps.player_name, mps.team_name,
               mps.first_kills, mps.first_deaths,
               mps.first_kills_atk, mps.first_kills_def,
               mps.first_deaths_atk, mps.first_deaths_def
        FROM map_player_stats mps
        JOIN matches m ON m.match_id = mps.match_id
        WHERE mps.team_name = ?
          AND {scope}
        """,
        [name, *sparams],
    ).fetchall()


def team_player_stats(conn, team_id, window=None, stage=None):
    """Return per-player per-map stat lines for this team within the window.

    One row per player per map the team played, framed by the detail-page team
    naming: match_id, player_name, player_id, agent, the per-map rating / acs /
    kills / deaths / assists / kast / adr / hs_pct, the first_kills / first_deaths,
    and the map's total rounds (both teams' scores summed) used to round-weight the
    rate stats and to turn kills and assists into per-round figures. The round
    count comes from map_results and is NULL when no result row is stored, in
    which case the aggregation skips that map's round-weighted contribution. The
    match_id lets stats.lineup_continuity tell maps apart when a map name repeats
    across matches. The date filter is on the parent match and the optional stage
    filter narrows to group or playoff play. Feeds stats.player_aggregates. Returns
    [] when the team has no stored detail.
    """
    name = _team_name(conn, team_id)
    if name is None:
        return []
    scope, sparams = _scope(window, None, stage, "m.")
    return conn.execute(
        f"""
        SELECT mps.match_id, mps.player_name, mps.player_id, mps.team_name,
               mps.map_name, mps.agent,
               mps.rating, mps.acs, mps.kills, mps.deaths, mps.assists,
               mps.kast, mps.adr, mps.hs_pct,
               mps.first_kills, mps.first_deaths,
               (mr.team1_score + mr.team2_score) AS map_rounds
        FROM map_player_stats mps
        JOIN matches m ON m.match_id = mps.match_id
        LEFT JOIN map_results mr
          ON mr.match_id = mps.match_id AND mr.map_name = mps.map_name
        WHERE mps.team_name = ?
          AND {scope}
        """,
        [name, *sparams],
    ).fetchall()


def team_compositions(conn, team_id, window=None, stage=None):
    """Per-map player agent rows for this team, with the map winner, windowed.

    One row per player per map the team played: match_id, map_name, team_name,
    agent, and the map's winner_name (joined from map_results). Feeds
    stats.map_compositions, which folds the per-player agents back into the
    five-agent composition the team fielded on each map. The date filter is on
    the parent match and the optional stage filter narrows to group or playoff
    play. Returns [] when the team has no stored detail.
    """
    name = _team_name(conn, team_id)
    if name is None:
        return []
    scope, sparams = _scope(window, None, stage, "m.")
    return conn.execute(
        f"""
        SELECT mps.match_id, mps.map_name, mps.team_name, mps.agent,
               mr.winner_name
        FROM map_player_stats mps
        JOIN matches m ON m.match_id = mps.match_id
        LEFT JOIN map_results mr
          ON mr.match_id = mps.match_id AND mr.map_name = mps.map_name
        WHERE mps.team_name = ?
          AND {scope}
        """,
        [name, *sparams],
    ).fetchall()


def team_clutches(conn, team_id, window=None, stage=None):
    """Per-player per-map clutch counts for this team, windowed.

    One row per player per map: player_name, team_name, clutch_won, clutch_lost.
    Feeds stats.clutch_stats. The columns are null until the scraper extension
    that fills them lands, so this returns rows with null counts (treated as zero
    by the aggregation) until then. The date filter is on the parent match and the
    optional stage filter narrows to group or playoff play. Returns [] when the
    team has no stored detail.
    """
    name = _team_name(conn, team_id)
    if name is None:
        return []
    scope, sparams = _scope(window, None, stage, "m.")
    return conn.execute(
        f"""
        SELECT mps.player_name, mps.team_name, mps.clutch_won, mps.clutch_lost
        FROM map_player_stats mps
        JOIN matches m ON m.match_id = mps.match_id
        WHERE mps.team_name = ?
          AND {scope}
        """,
        [name, *sparams],
    ).fetchall()


def team_economy(conn, team_id, window=None, stage=None):
    """Per-round economy rows for this team's maps, windowed.

    One row per stored economy entry: team_name, buy_type, outcome. Feeds
    stats.economy_conversion. The economy table is empty until the upstream
    per-map economy scrape is fixed, so this returns [] on real data today; the
    query and aggregation are in place so the view fills when the data does. The
    date filter is on the parent match and the optional stage filter narrows to
    group or playoff play.
    """
    name = _team_name(conn, team_id)
    if name is None:
        return []
    scope, sparams = _scope(window, None, stage, "m.")
    return conn.execute(
        f"""
        SELECT e.team_name, e.buy_type, e.outcome
        FROM economy e
        JOIN matches m ON m.match_id = e.match_id
        WHERE e.team_name = ?
          AND {scope}
        """,
        [name, *sparams],
    ).fetchall()


def team_map_opponent_rank(conn, team_id, window=None, stage=None):
    """Average opponent regional rank per map this team played, windowed.

    Tells the user the quality of opposition behind a per-map figure, so a 70%
    on Bind farmed against weak teams reads differently from one against tier
    one (item 5). One row per map: map_name, avg_rank (average opponent regional
    rank, None when no opponent there had a stored rank), ranked (how many of the
    maps had a ranked opponent), and maps (total). The rank is VLR's current
    snapshot applied to past maps, so it is a rough signal, like the
    strength-of-schedule figure. The team can be in either match slot, so the
    opponent slot is resolved by id. Returns [] when the team has no detail.
    """
    name = _team_name(conn, team_id)
    if name is None:
        return []
    scope, sparams = _scope(window, None, stage, "m.")
    rows = conn.execute(
        f"""
        SELECT mr.map_name,
               AVG(t.regional_rank) AS avg_rank,
               COUNT(t.regional_rank) AS ranked,
               COUNT(*) AS maps
        FROM map_results mr
        JOIN matches m ON m.match_id = mr.match_id
        LEFT JOIN teams t ON t.id = (
            CASE WHEN m.team1_id = ? THEN m.team2_id ELSE m.team1_id END
        )
        WHERE (mr.team1_name = ? OR mr.team2_name = ?)
          AND {scope}
        GROUP BY mr.map_name
        """,
        [team_id, name, name, *sparams],
    ).fetchall()
    return rows


def player_appearances(conn, team_id, window=None, stage=None):
    """When each player appeared for this team, from stored per-map detail.

    The transactions endpoint is unreliable, so roster change history is derived
    from who actually played: one row per player who has a map for this team in
    the window, with their first and last appearance date and their map count.
    Newest last appearance first, so the current names surface at the top.
    Resolves the team id to the detail-page name like the other detail queries.
    The optional stage filter narrows to group or playoff play.
    """
    name = _team_name(conn, team_id)
    if name is None:
        return []
    scope, sparams = _scope(window, None, stage, "m.")
    return conn.execute(
        f"""
        SELECT mps.player_name,
               MIN(m.date) AS first_date,
               MAX(m.date) AS last_date,
               COUNT(DISTINCT mps.match_id) AS matches,
               COUNT(*) AS maps
        FROM map_player_stats mps
        JOIN matches m ON m.match_id = mps.match_id
        WHERE mps.team_name = ?
          AND {scope}
        GROUP BY mps.player_name
        ORDER BY last_date DESC, maps DESC
        """,
        [name, *sparams],
    ).fetchall()


def team_vetos(conn, team_id, window=None):
    """Return the stored veto actions for matches this team played, windowed.

    One row per veto action (ban, pick, or remains) across the team's matches,
    in match and sequence order: match_id, seq, team_token, action, map_name. The
    team can be in either match slot, so both are checked. The date filter is on
    the parent match. Feeds valtrack.veto.team_tendencies, which resolves the tag
    token and filters to real maps. Returns [] when the team has no veto data.
    """
    window = window or DateWindow.all_time()
    wclause, wparams = window.clause("m.date")
    return conn.execute(
        f"""
        SELECT v.match_id, v.seq, v.team_token, v.action, v.map_name,
               m.date AS match_date
        FROM match_vetos v
        JOIN matches m ON m.match_id = v.match_id
        WHERE (m.team1_id = ? OR m.team2_id = ?)
          AND {wclause}
        ORDER BY v.match_id, v.seq
        """,
        [team_id, team_id, *wparams],
    ).fetchall()


def head_to_head(conn, team_a_id, team_b_id, window=None, events=None, stage=None):
    """The direct record and meetings between two teams, framed from A.

    Finds matches these two teams played against each other (either slot), within
    the window, event, and stage filters, and returns each decided meeting newest
    first plus the head-to-head record. Ties and undecided rows drop out. Returns a
    dict with a_wins, b_wins, decided, and a meetings list (match_id, date, event,
    scores from A's point of view, and which side won).
    """
    scope, sparams = _scope(window, events, stage)
    rows = conn.execute(
        f"""
        SELECT match_id, date, event_round, event_name,
               team1_id, team1_name, team1_score,
               team2_id, team2_name, team2_score
        FROM matches
        WHERE ((team1_id = ? AND team2_id = ?) OR (team1_id = ? AND team2_id = ?))
          AND team1_score IS NOT NULL
          AND team2_score IS NOT NULL
          AND team1_score != team2_score
          AND {scope}
        ORDER BY date DESC, match_id DESC
        """,
        [team_a_id, team_b_id, team_b_id, team_a_id, *sparams],
    ).fetchall()
    meetings = []
    a_wins = b_wins = 0
    for r in rows:
        if r["team1_id"] == team_a_id:
            a_score, b_score = r["team1_score"], r["team2_score"]
        else:
            a_score, b_score = r["team2_score"], r["team1_score"]
        winner = "a" if a_score > b_score else "b"
        if winner == "a":
            a_wins += 1
        else:
            b_wins += 1
        meetings.append({
            "match_id": r["match_id"],
            "date": r["date"],
            "event": r["event_name"] or r["event_round"],
            "a_score": a_score,
            "b_score": b_score,
            "winner": winner,
        })
    return {
        "a_wins": a_wins, "b_wins": b_wins,
        "decided": a_wins + b_wins, "meetings": meetings,
    }


def schedule_strength(conn, team_id, window=None, events=None, stage=None):
    """A team's strength of schedule: average opponent rank over the window.

    For each decided match, the opponent is looked up in the stored teams to read
    its current regional rank. Returns the average of those ranks, how many
    opponents were ranked, and the total decided matches. Only stored teams carry
    a rank (mostly other franchise teams), and the rank is VLR's current snapshot
    applied to past matches, so this is a rough strength signal, not exact. A None
    average means no opponent in range had a stored rank. The optional event and
    stage filters narrow to LAN or online and to group or playoff play.
    """
    scope, sparams = _scope(window, events, stage, "m.")
    rows = conn.execute(
        f"""
        SELECT t.regional_rank AS rank
        FROM matches m
        LEFT JOIN teams t ON t.id = (
            CASE WHEN m.team1_id = ? THEN m.team2_id ELSE m.team1_id END
        )
        WHERE (m.team1_id = ? OR m.team2_id = ?)
          AND m.team1_score IS NOT NULL
          AND m.team2_score IS NOT NULL
          AND m.team1_score != m.team2_score
          AND {scope}
        """,
        [team_id, team_id, team_id, *sparams],
    ).fetchall()
    ranks = [r["rank"] for r in rows if r["rank"] is not None]
    avg = sum(ranks) / len(ranks) if ranks else None
    return {"avg_opp_rank": avg, "ranked": len(ranks), "decided": len(rows)}


def _opponent_records(conn, team_id, window, events, stage=None):
    """A team's decided record against each opponent, keyed by opponent name."""
    scope, sparams = _scope(window, events, stage)
    rows = conn.execute(
        f"""
        SELECT
            CASE WHEN team1_id = ? THEN team2_name ELSE team1_name END AS opponent,
            CASE WHEN team1_id = ?
                 THEN (CASE WHEN team1_score > team2_score THEN 'W' ELSE 'L' END)
                 ELSE (CASE WHEN team2_score > team1_score THEN 'W' ELSE 'L' END)
            END AS result
        FROM matches
        WHERE (team1_id = ? OR team2_id = ?)
          AND team1_score IS NOT NULL
          AND team2_score IS NOT NULL
          AND team1_score != team2_score
          AND {scope}
        """,
        [team_id, team_id, team_id, team_id, *sparams],
    ).fetchall()
    recs = {}
    for r in rows:
        opp = r["opponent"]
        if not opp:
            continue
        agg = recs.setdefault(opp, {"wins": 0, "losses": 0})
        if r["result"] == "W":
            agg["wins"] += 1
        else:
            agg["losses"] += 1
    return recs


def common_opponents(conn, team_a_id, team_b_id, window=None, events=None,
                     stage=None):
    """Opponents both teams have faced, with each team's record against them.

    Most useful across regions, where the two teams rarely meet but may share
    results against the same third teams. Each team's own name and the other
    selected team are excluded, since the point is a shared third opponent.
    Returns a list of {opponent, a, b} where a and b are {wins, losses}, ordered
    by the combined number of decided matches so the best-sampled rows lead. The
    optional event and stage filters narrow to LAN or online and group or playoff.
    """
    a = _opponent_records(conn, team_a_id, window, events, stage)
    b = _opponent_records(conn, team_b_id, window, events, stage)
    a_name = _team_name(conn, team_a_id)
    b_name = _team_name(conn, team_b_id)
    excluded = {a_name, b_name}
    out = []
    for opp in set(a) & set(b):
        if opp in excluded:
            continue
        out.append({"opponent": opp, "a": a[opp], "b": b[opp]})
    out.sort(
        key=lambda x: -(x["a"]["wins"] + x["a"]["losses"]
                        + x["b"]["wins"] + x["b"]["losses"])
    )
    return out


def last_match_date(conn, team_id):
    """The team's most recent match date over all stored matches, or None.

    Used for the stale-data flag, which is about real elapsed time since the team
    last played, so it ignores the comparison window. The team can be in either
    slot. Returns None when the team has no dated matches.
    """
    row = conn.execute(
        """
        SELECT MAX(date) AS last
        FROM matches
        WHERE (team1_id = ? OR team2_id = ?) AND date IS NOT NULL
        """,
        (team_id, team_id),
    ).fetchone()
    return row["last"] if row else None


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


def team_series_results(conn, team_id, window=None, stage=None):
    """Per-map results for the team's series, with the series score, for pressure.

    One row per map the team played: match_id, map_order, the map winner_name, and
    the series scores from this team's point of view (team_series_score and
    opp_series_score, taken from the parent match and identical across a match's
    rows). The team's slot is resolved by id so the series score is always read
    from the right side. The date filter is on the parent match and the optional
    stage filter narrows to group or playoff play. Feeds stats.pressure_stats.
    Returns [] when the team has no stored detail.
    """
    name = _team_name(conn, team_id)
    if name is None:
        return []
    scope, sparams = _scope(window, None, stage, "m.")
    return conn.execute(
        f"""
        SELECT mr.match_id, mr.map_order, mr.winner_name,
               CASE WHEN m.team1_id = ? THEN m.team1_score ELSE m.team2_score END
                   AS team_series_score,
               CASE WHEN m.team1_id = ? THEN m.team2_score ELSE m.team1_score END
                   AS opp_series_score
        FROM map_results mr
        JOIN matches m ON m.match_id = mr.match_id
        WHERE (mr.team1_name = ? OR mr.team2_name = ?)
          AND {scope}
        """,
        [team_id, team_id, name, name, *sparams],
    ).fetchall()


def meeting_maps(conn, match_id):
    """The per-map results for a single match, in map order, for the H2H detail.

    One row per map: map_name, map_order, the two side names and scores, and the
    winner. Returns [] when no per-map detail is stored for the match, which is
    the honest empty state for an older meeting the detail harvest has not reached.
    """
    return conn.execute(
        """
        SELECT map_name, map_order, team1_name, team2_name,
               team1_score, team2_score, winner_name
        FROM map_results
        WHERE match_id = ?
        ORDER BY CASE WHEN map_order IS NULL THEN 1 ELSE 0 END, map_order
        """,
        (match_id,),
    ).fetchall()


def meeting_lineup(conn, match_id, team_name):
    """The distinct players a team actually fielded in one match, for H2H detail.

    Reading the lineup from who played sidesteps the unreliable roster-change
    table and reflects the side that actually took the server that day. Returns a
    list of player names, or [] when no per-map detail is stored for the match.
    """
    rows = conn.execute(
        """
        SELECT DISTINCT player_name
        FROM map_player_stats
        WHERE match_id = ? AND team_name = ? AND player_name IS NOT NULL
        ORDER BY player_name COLLATE NOCASE
        """,
        (match_id, team_name),
    ).fetchall()
    return [r["player_name"] for r in rows]


def detail_coverage(conn, team_id, window=None, events=None, stage=None):
    """How many of a team's in-range matches carry per-match detail.

    Returns {detailed, total} where total is the team's matches in the window,
    event, and stage filters, and detailed is how many of those have had the
    expensive per-match pass stored (details_fetched_at set). This is what tells
    the user how complete a detail-derived figure actually is, rather than leaving
    the partial harvest invisible.
    """
    scope, sparams = _scope(window, events, stage)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN details_fetched_at IS NOT NULL THEN 1 ELSE 0 END)
                   AS detailed
        FROM matches
        WHERE (team1_id = ? OR team2_id = ?)
          AND {scope}
        """,
        [team_id, team_id, *sparams],
    ).fetchone()
    return {"total": row["total"] or 0, "detailed": row["detailed"] or 0}


def team_window_summary(conn, team_id, window=None, events=None, stage=None):
    """A one-line summary of how much data backs a team's column.

    Returns {total, decided, min_date, max_date} over the team's matches in the
    window, event, and stage filters, so the app can say up front how many matches
    and what date span sit behind everything below. The team can be in either slot.
    """
    scope, sparams = _scope(window, events, stage)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS total,
               SUM(CASE
                   WHEN team1_score IS NOT NULL AND team2_score IS NOT NULL
                        AND team1_score != team2_score THEN 1 ELSE 0 END) AS decided,
               MIN(date) AS mn, MAX(date) AS mx
        FROM matches
        WHERE (team1_id = ? OR team2_id = ?)
          AND {scope}
        """,
        [team_id, team_id, *sparams],
    ).fetchone()
    return {
        "total": row["total"] or 0,
        "decided": row["decided"] or 0,
        "min_date": row["mn"],
        "max_date": row["mx"],
    }


def head_to_head_maps(conn, team_a_id, team_b_id, window=None, events=None,
                      stage=None):
    """Per-map breakdown of the two teams' direct meetings, with how old it is.

    A 3-1 head-to-head reads very differently if it is spread across maps or
    concentrated on one the teams keep returning to, and differently again if the
    maps were all played over a year ago. This returns, for each map the two teams
    actually played against each other (within the window, event, and stage
    filters), how many times it was played, how the map wins split between A and B,
    and the date it was last played, newest-sampled maps first.

    Side win rates are deliberately left out: a head-to-head sample is usually a
    handful of maps, and a per-side rate over one or two maps is noise, the kind of
    misleading number the charter asks us not to show. Returns [] when no per-map
    detail is stored for the meetings (the honest empty state for franchise
    cross-region pairs, who often have no head-to-head at all).
    """
    a_name = _team_name(conn, team_a_id)
    b_name = _team_name(conn, team_b_id)
    if a_name is None or b_name is None:
        return []
    scope, sparams = _scope(window, events, stage, "m.")
    rows = conn.execute(
        f"""
        SELECT mr.map_name,
               COUNT(*) AS played,
               SUM(CASE WHEN mr.winner_name = ? THEN 1 ELSE 0 END) AS a_wins,
               SUM(CASE WHEN mr.winner_name = ? THEN 1 ELSE 0 END) AS b_wins,
               MAX(m.date) AS last_date
        FROM map_results mr
        JOIN matches m ON m.match_id = mr.match_id
        WHERE ((m.team1_id = ? AND m.team2_id = ?)
               OR (m.team1_id = ? AND m.team2_id = ?))
          AND mr.map_name IS NOT NULL
          AND {scope}
        GROUP BY mr.map_name
        ORDER BY played DESC, last_date DESC
        """,
        [a_name, b_name, team_a_id, team_b_id, team_b_id, team_a_id, *sparams],
    ).fetchall()
    return rows


def team_rest_load(conn, team_id, today=None):
    """Days since the team last played and its recent match and map load.

    Below the stale-data threshold there is still a real difference between a team
    that has had two weeks off to prep and one that just played eight maps over a
    long weekend, and for reading an actual upcoming match that competitive
    condition matters. This is plain date arithmetic over the stored matches, not a
    performance figure: it belongs to the context cluster and says nothing about
    why a team is rested or busy. `today` is injectable for tests.

    Returns days_since (None when the team has no dated match), the last date, and
    the count of matches and maps in the last 14 and 30 days.
    """
    today = today or date.today()
    today_s = today.isoformat()
    d14 = (today - timedelta(days=14)).isoformat()
    d30 = (today - timedelta(days=30)).isoformat()
    row = conn.execute(
        """
        SELECT MAX(date) AS last_date,
               SUM(CASE WHEN date >= ? AND date <= ? THEN 1 ELSE 0 END) AS m14,
               SUM(CASE WHEN date >= ? AND date <= ? THEN 1 ELSE 0 END) AS m30
        FROM matches
        WHERE (team1_id = ? OR team2_id = ?) AND date IS NOT NULL
        """,
        [d14, today_s, d30, today_s, team_id, team_id],
    ).fetchone()
    maps14 = maps30 = 0
    name = _team_name(conn, team_id)
    if name is not None:
        mr = conn.execute(
            """
            SELECT SUM(CASE WHEN m.date >= ? AND m.date <= ? THEN 1 ELSE 0 END) AS p14,
                   SUM(CASE WHEN m.date >= ? AND m.date <= ? THEN 1 ELSE 0 END) AS p30
            FROM map_results mr
            JOIN matches m ON m.match_id = mr.match_id
            WHERE (mr.team1_name = ? OR mr.team2_name = ?)
            """,
            [d14, today_s, d30, today_s, name, name],
        ).fetchone()
        maps14, maps30 = mr["p14"] or 0, mr["p30"] or 0
    last_date = row["last_date"]
    days_since = ((today - date.fromisoformat(last_date)).days
                  if last_date else None)
    return {
        "last_date": last_date, "days_since": days_since,
        "matches_14": row["m14"] or 0, "matches_30": row["m30"] or 0,
        "maps_14": maps14, "maps_30": maps30,
    }


def last_roster_change_date(conn, team_id, five_names):
    """An approximate date of the team's last roster change, from appearances.

    The transactions endpoint is unreliable, so this derives the change date from
    who has actually played: the most recent first appearance among the current
    five is when the newest current starter debuted, which approximates the last
    change to the lineup that will play. It is deliberately labeled approximate
    upstream rather than presented as an exact transfer date. Returns an ISO date
    string, or None when there is no current five or no appearance to read.
    """
    if not five_names:
        return None
    rows = player_appearances(conn, team_id)
    firsts = [
        r["first_date"] for r in rows
        if (r["player_name"] or "").casefold() in five_names and r["first_date"]
    ]
    return max(firsts) if firsts else None
