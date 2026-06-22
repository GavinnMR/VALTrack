"""Read-side queries for the VALTrack app.

The Streamlit app is a presentation layer and holds no SQL of its own. These
helpers read the stored teams and matches and hand back plain rows the UI can
render. Keeping the queries here also lets the few computed figures, such as a
team's overall record, be unit tested without standing up the app.

Every function takes an open connection, matching the ingestion side, so a test
can pass a temporary database.
"""


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
    """Return one team's identity and ranking row, or None if it is not stored."""
    return conn.execute(
        """
        SELECT id, name, tag, logo, league, region,
               regional_rank, world_rank, rating, streak
        FROM teams
        WHERE id = ?
        """,
        (team_id,),
    ).fetchone()


def team_record(conn, team_id):
    """Compute a team's overall series record across all stored matches.

    A franchise team can be stored in either the team1 or the team2 slot of a
    match, because the same match is pulled from both teams' histories and the
    last write wins the team1 slot. So we look in both slots and compare that
    side's score against the opponent's. A match is only counted as decided when
    both scores are present and unequal, so ties and unplayed rows drop out.

    Returns a dict with wins, losses, and decided (wins + losses).
    """
    row = conn.execute(
        """
        SELECT
            SUM(CASE
                WHEN team1_id = :tid AND team1_score > team2_score THEN 1
                WHEN team2_id = :tid AND team2_score > team1_score THEN 1
                ELSE 0 END) AS wins,
            SUM(CASE
                WHEN team1_id = :tid AND team1_score < team2_score THEN 1
                WHEN team2_id = :tid AND team2_score < team1_score THEN 1
                ELSE 0 END) AS losses
        FROM matches
        WHERE (team1_id = :tid OR team2_id = :tid)
          AND team1_score IS NOT NULL
          AND team2_score IS NOT NULL
        """,
        {"tid": team_id},
    ).fetchone()
    wins = row["wins"] or 0
    losses = row["losses"] or 0
    return {"wins": wins, "losses": losses, "decided": wins + losses}
