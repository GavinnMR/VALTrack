"""Tests for the match-history paging in the ingestion engine.

The endpoint does not return an empty page at the end of a team's history. Past
the last real page it repeats placeholder junk whose match_id is the team id and
whose fields are blank. These tests lock in that we stop at the true end and
never store the junk rows.
"""
from valtrack import db
from valtrack.ingest import (
    ingest_team_matches,
    matches_missing_analytics,
    matches_needing_detail,
    resolve_ranking,
)


def _real_match(match_id, opponent="Opponent", score="2:1"):
    return {
        "match_id": str(match_id),
        "url": f"https://www.vlr.gg/{match_id}/x",
        "event": "Some Event",
        "date": "2026/01/01",
        "time": "8:00 am",
        "team1": {"name": "Sentinels", "tag": "SEN", "logo": ""},
        "team2": {"name": opponent, "tag": "OPP", "logo": ""},
        "score": score,
        "result": "win",
    }


def _junk_segment(team_id):
    # Exactly what vlrggapi emits past the last page: id is the team id, blanks.
    return {
        "match_id": str(team_id),
        "url": f"https://www.vlr.gg/team/matches/{team_id}/sentinels/",
        "event": "",
        "date": "",
        "time": "",
        "team1": {"name": "", "tag": "", "logo": ""},
        "team2": {"name": "", "tag": "", "logo": ""},
        "score": "",
        "result": "",
    }


class FakeClient:
    """Serves preset real pages, then repeats a junk page forever like the API."""

    def __init__(self, real_pages, team_id):
        self.real_pages = real_pages
        self.junk = [_junk_segment(team_id)] * 10
        self.calls = []

    def team_matches(self, team_id, page=1):
        self.calls.append(page)
        if page in self.real_pages:
            return {"segments": self.real_pages[page]}
        # Clamp-and-repeat: never empty, always the same junk past the end.
        return {"segments": self.junk}


def _fresh_conn(tmp_path):
    path = tmp_path / "t.db"
    db.init_db(path)
    return db.connect(path)


def test_full_stops_at_end_and_skips_junk(tmp_path):
    team_id = 2
    real_pages = {
        1: [_real_match(1001), _real_match(1002)],
        2: [_real_match(1003)],
    }
    client = FakeClient(real_pages, team_id)
    conn = _fresh_conn(tmp_path)

    written = ingest_team_matches(client, conn, team_id, scope="full")
    conn.commit()

    assert written == 3
    # Page 3 is the first junk page; it yields nothing new, so we stop there.
    assert client.calls == [1, 2, 3]
    # The junk row (match_id == team_id) must never be stored.
    assert conn.execute(
        "SELECT COUNT(*) FROM matches WHERE match_id = ?", (team_id,)
    ).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 3


def test_full_never_runs_away_to_max_pages(tmp_path):
    # A team whose very first page is already past the end (no real matches).
    team_id = 17
    client = FakeClient({}, team_id)
    conn = _fresh_conn(tmp_path)

    written = ingest_team_matches(client, conn, team_id, scope="full", max_pages=100)

    assert written == 0
    assert client.calls == [1]


def test_incremental_stops_at_first_known_match(tmp_path):
    team_id = 2
    real_pages = {
        1: [_real_match(2001), _real_match(2002), _real_match(2003)],
        2: [_real_match(2004)],
    }
    client = FakeClient(real_pages, team_id)
    conn = _fresh_conn(tmp_path)

    # Seed a known match that sits in the middle of page 1.
    ingest_team_matches(client, conn, team_id, scope="full")
    conn.commit()
    # New run: pretend everything from 2002 onward is already stored by deleting
    # the newer rows, leaving 2002 as the newest known.
    conn.execute("DELETE FROM matches WHERE match_id = 2001")
    conn.commit()
    client.calls.clear()

    written = ingest_team_matches(client, conn, team_id, scope="incremental")
    conn.commit()

    # 2001 is newest and unknown, then 2002 is known and halts the run.
    assert written == 1
    assert client.calls == [1]
    conn.close()


class FakeRankingsClient:
    """Serves preset ranking ladders by region code."""

    def __init__(self, ladders):
        self.ladders = ladders
        self.fetched = []

    def rankings(self, region):
        self.fetched.append(region)
        rows = self.ladders.get(region, [])
        segments = [
            {"team": name, "rank": rank, "record": "1-0", "earnings": "$0"}
            for name, rank in rows
        ]
        return {"segments": segments}


def test_resolve_ranking_searches_league_ladders_in_order():
    # Americas spans na, br, la-s. Each franchise team sits on its own ladder.
    client = FakeRankingsClient(
        {
            "na": [("Sentinels", 6), ("Leviatán", 1)],
            "br": [("LOUD", 5), ("MIBR", 3)],
            "la-s": [("KRÜ Esports", 3)],
        }
    )
    cache = {}

    # Brazilian team is found on the br ladder, not the first one searched.
    loud = resolve_ranking(client, cache, "americas", "LOUD", "LOUD")
    assert loud["regional_rank"] == 5
    # LATAM-south team is found further down the priority list.
    kru = resolve_ranking(client, cache, "americas", "KRÜ Esports", "KRÜ Esports")
    assert kru["regional_rank"] == 3
    # Leviatán sits on the na ladder even though it is an Americas team.
    levi = resolve_ranking(client, cache, "americas", "LEVIATÁN", "Leviatán")
    assert levi["regional_rank"] == 1


def test_resolve_ranking_returns_none_for_unranked_team():
    client = FakeRankingsClient({"eu": [("FNATIC", 1)]})
    cache = {}
    # An inactive org on no ladder yields None rather than a fabricated rank.
    assert resolve_ranking(client, cache, "emea", "Apeks", "Apeks") is None


def test_resolve_ranking_prefers_earlier_ladder_on_collision():
    client = FakeRankingsClient(
        {"na": [("Ghost", 2)], "br": [("Ghost", 9)]}
    )
    cache = {}
    hit = resolve_ranking(client, cache, "americas", "Ghost", "Ghost")
    assert hit["regional_rank"] == 2  # na is searched before br


def _detail_match(conn, match_id, mdate, detailed=False, maps=False, econ=False):
    """A match row plus optional map_results and map_economy, for selection tests."""
    conn.execute(
        "INSERT INTO matches (match_id, team1_score, team2_score, date, "
        "details_fetched_at) VALUES (?, 2, 1, ?, ?)",
        (match_id, mdate, "2026-01-01T00:00:00+00:00" if detailed else None),
    )
    if maps:
        conn.execute(
            "INSERT INTO map_results (match_id, map_name, winner_name) "
            "VALUES (?, 'Ascent', 'A')",
            (match_id,),
        )
    if econ:
        conn.execute(
            "INSERT INTO map_economy (match_id, map_name, team_name, buy_type, "
            "played, won) VALUES (?, 'Ascent', 'A', 'eco', 3, 1)",
            (match_id,),
        )


def test_matches_needing_detail_since_bounds_the_window(tmp_path):
    conn = _fresh_conn(tmp_path)
    _detail_match(conn, 1, "2024-01-01")  # old, undetailed
    _detail_match(conn, 2, "2026-05-01")  # recent, undetailed
    conn.execute(
        "INSERT INTO matches (match_id, team1_score, team2_score, date) "
        "VALUES (3, NULL, NULL, '2026-05-02')"  # undecided, never selected
    )
    conn.commit()

    assert matches_needing_detail(conn) == [2, 1]
    assert matches_needing_detail(conn, since="2025-01-01") == [2]
    conn.close()


def test_matches_missing_analytics_picks_predetail_and_new_only(tmp_path):
    conn = _fresh_conn(tmp_path)
    # Detailed before the economy table existed: has maps, no economy -> reselect.
    _detail_match(conn, 1, "2026-05-01", detailed=True, maps=True, econ=False)
    # Detailed with the patched scraper: has economy -> done, not reselected.
    _detail_match(conn, 2, "2026-05-02", detailed=True, maps=True, econ=True)
    # A forfeit: detailed, no maps -> not reselected (nothing to fill).
    _detail_match(conn, 3, "2026-05-03", detailed=True, maps=False, econ=False)
    # Brand-new, never detailed -> selected.
    _detail_match(conn, 4, "2026-05-04", detailed=False)
    conn.commit()

    got = matches_missing_analytics(conn)
    assert set(got) == {1, 4}
    # Newest first.
    assert got == [4, 1]
    conn.close()


def test_matches_missing_analytics_since_bounds_the_window(tmp_path):
    conn = _fresh_conn(tmp_path)
    _detail_match(conn, 1, "2023-01-01", detailed=True, maps=True, econ=False)
    _detail_match(conn, 2, "2026-05-01", detailed=True, maps=True, econ=False)
    conn.commit()

    assert matches_missing_analytics(conn, since="2025-01-01") == [2]
    conn.close()


def test_duplicate_real_rows_across_pages_do_not_loop(tmp_path):
    # If a clamped page repeats real rows already seen, we still terminate.
    team_id = 2
    repeated = [_real_match(3001), _real_match(3002)]
    real_pages = {1: repeated, 2: repeated}
    client = FakeClient(real_pages, team_id)
    conn = _fresh_conn(tmp_path)

    written = ingest_team_matches(client, conn, team_id, scope="full")
    conn.commit()

    assert written == 2
    # Page 2 repeats page 1's ids, contributing nothing new, so we stop.
    assert client.calls == [1, 2]
