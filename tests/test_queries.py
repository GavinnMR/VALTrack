"""Tests for the read-side queries.

team_record and the windowed match reads are the aggregation in this slice. The
tricky parts: a franchise team can be stored in either match slot, the date
window must filter correctly, and form and recent history must read each result
from the team's own point of view.
"""
from datetime import date

from valtrack import db
from valtrack.window import DateWindow


def _fresh_conn(tmp_path):
    path = tmp_path / "t.db"
    db.init_db(path)
    return db.connect(path)


def _add_match(conn, match_id, t1_id, t2_id, t1_score, t2_score, match_date=None):
    conn.execute(
        """
        INSERT INTO matches (match_id, team1_id, team2_id,
                             team1_score, team2_score, date)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (match_id, t1_id, t2_id, t1_score, t2_score, match_date),
    )


def _add_named_match(conn, match_id, t1_id, t1_name, t2_id, t2_name,
                     t1_score, t2_score, match_date, rnd="R1",
                     t1_tag=None, t2_tag=None):
    conn.execute(
        """
        INSERT INTO matches (match_id, date, event_round,
            team1_id, team1_name, team1_tag, team1_score,
            team2_id, team2_name, team2_tag, team2_score)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (match_id, match_date, rnd, t1_id, t1_name, t1_tag, t1_score,
         t2_id, t2_name, t2_tag, t2_score),
    )


def test_record_counts_team_in_either_slot(tmp_path):
    from valtrack.queries import team_record

    conn = _fresh_conn(tmp_path)
    me, opp = 100, 200
    # As team1: one win, one loss.
    _add_match(conn, 1, me, opp, 2, 0)
    _add_match(conn, 2, me, opp, 1, 2)
    # As team2: one win, one loss. The slot must not change the result.
    _add_match(conn, 3, opp, me, 0, 2)
    _add_match(conn, 4, opp, me, 2, 1)
    conn.commit()

    rec = team_record(conn, me)
    assert rec == {"wins": 2, "losses": 2, "decided": 4}
    conn.close()


def test_record_ignores_unplayed_and_tied_matches(tmp_path):
    from valtrack.queries import team_record

    conn = _fresh_conn(tmp_path)
    me, opp = 100, 200
    _add_match(conn, 1, me, opp, 2, 0)        # counts as a win
    _add_match(conn, 2, me, opp, None, None)  # no scores yet, ignored
    _add_match(conn, 3, me, opp, 1, None)     # half a score, ignored
    _add_match(conn, 4, me, opp, 1, 1)        # tie, decided by neither side
    conn.commit()

    rec = team_record(conn, me)
    assert rec == {"wins": 1, "losses": 0, "decided": 1}
    conn.close()


def test_record_excludes_other_teams_matches(tmp_path):
    from valtrack.queries import team_record

    conn = _fresh_conn(tmp_path)
    me, opp, other = 100, 200, 300
    _add_match(conn, 1, me, opp, 2, 1)        # mine
    _add_match(conn, 2, other, opp, 2, 0)     # not mine
    conn.commit()

    rec = team_record(conn, me)
    assert rec == {"wins": 1, "losses": 0, "decided": 1}
    conn.close()


def test_record_is_zero_for_team_with_no_matches(tmp_path):
    from valtrack.queries import team_record

    conn = _fresh_conn(tmp_path)
    rec = team_record(conn, 999)
    assert rec == {"wins": 0, "losses": 0, "decided": 0}
    conn.close()


def test_record_respects_window(tmp_path):
    from valtrack.queries import team_record

    conn = _fresh_conn(tmp_path)
    me, opp = 100, 200
    _add_match(conn, 1, me, opp, 2, 0, "2024-01-10")   # win, in range
    _add_match(conn, 2, me, opp, 0, 2, "2024-02-10")   # loss, in range
    _add_match(conn, 3, me, opp, 2, 1, "2023-12-01")   # win, before range
    _add_match(conn, 4, me, opp, 2, 1, "2024-09-01")   # win, after range
    conn.commit()

    w = DateWindow(date(2024, 1, 1), date(2024, 6, 30))
    assert team_record(conn, me, w) == {"wins": 1, "losses": 1, "decided": 2}
    # All time still sees everything.
    assert team_record(conn, me) == {"wins": 3, "losses": 1, "decided": 4}
    conn.close()


def test_decided_results_newest_first_and_windowed(tmp_path):
    from valtrack.queries import decided_results

    conn = _fresh_conn(tmp_path)
    me, opp = 100, 200
    _add_match(conn, 1, me, opp, 2, 0, "2024-01-10")       # W
    _add_match(conn, 2, opp, me, 2, 0, "2024-02-10")       # L (me is team2)
    _add_match(conn, 3, me, opp, 1, 1, "2024-03-10")       # tie, excluded
    _add_match(conn, 4, me, opp, None, None, "2024-04-10")  # undecided, excluded
    _add_match(conn, 5, me, opp, 2, 1, "2024-05-10")       # W
    conn.commit()

    assert decided_results(conn, me) == ["W", "L", "W"]
    w = DateWindow(date(2024, 2, 1), None)
    assert decided_results(conn, me, w) == ["W", "L"]
    conn.close()


def test_recent_matches_perspective_and_limit(tmp_path):
    from valtrack.queries import recent_matches

    conn = _fresh_conn(tmp_path)
    me, opp = 100, 200
    _add_named_match(conn, 1, me, "Me", opp, "Them", 2, 0, "2024-01-10", t2_tag="THM")
    _add_named_match(conn, 2, opp, "Them", me, "Me", 2, 1, "2024-02-10", t1_tag="THM")
    conn.commit()

    rec = recent_matches(conn, me, limit=10)
    # Newest first, framed from me's point of view.
    assert rec[0]["match_id"] == 2
    assert rec[0]["opponent"] == "Them"
    assert rec[0]["opponent_tag"] == "THM"
    assert rec[0]["score"] == (1, 2)   # me is team2 with 1, opponent has 2
    assert rec[0]["result"] == "L"
    assert rec[1]["opponent"] == "Them"
    assert rec[1]["score"] == (2, 0)
    assert rec[1]["result"] == "W"
    assert len(recent_matches(conn, me, limit=1)) == 1
    conn.close()


def _add_team(conn, team_id, name):
    conn.execute("INSERT INTO teams (id, name) VALUES (?, ?)", (team_id, name))


def _add_map_result(conn, match_id, map_name, t1_name, t2_name, winner):
    conn.execute(
        """
        INSERT INTO map_results (match_id, map_name, team1_name, team2_name,
                                 winner_name)
        VALUES (?, ?, ?, ?, ?)
        """,
        (match_id, map_name, t1_name, t2_name, winner),
    )


def _add_round(conn, match_id, map_name, side, winner_team):
    conn.execute(
        """
        INSERT INTO rounds (match_id, map_name, winner_side, winner_team)
        VALUES (?, ?, ?, ?)
        """,
        (match_id, map_name, side, winner_team),
    )


def test_team_map_results_resolves_name_and_window(tmp_path):
    from valtrack.queries import team_map_results

    conn = _fresh_conn(tmp_path)
    _add_team(conn, 100, "Alpha")
    _add_team(conn, 200, "Beta")
    # Alpha plays in either detail slot; the date lives on the parent match.
    _add_match(conn, 1, 100, 200, 2, 0, "2024-01-10")
    _add_map_result(conn, 1, "Ascent", "Alpha", "Beta", "Alpha")
    _add_match(conn, 2, 200, 100, 2, 1, "2024-05-10")
    _add_map_result(conn, 2, "Bind", "Beta", "Alpha", "Beta")
    # A map from a match outside Alpha entirely must not appear.
    _add_match(conn, 3, 200, 300, 2, 0, "2024-03-10")
    _add_map_result(conn, 3, "Lotus", "Beta", "Gamma", "Beta")
    conn.commit()

    rows = team_map_results(conn, 100)
    assert sorted(r["map_name"] for r in rows) == ["Ascent", "Bind"]

    w = DateWindow(date(2024, 1, 1), date(2024, 3, 1))
    rows = team_map_results(conn, 100, w)
    assert [r["map_name"] for r in rows] == ["Ascent"]
    conn.close()


def test_team_rounds_scoped_to_teams_maps_and_windowed(tmp_path):
    from valtrack.queries import team_rounds

    conn = _fresh_conn(tmp_path)
    _add_team(conn, 100, "Alpha")
    _add_match(conn, 1, 100, 200, 2, 0, "2024-01-10")
    _add_map_result(conn, 1, "Ascent", "Alpha", "Beta", "Alpha")
    _add_round(conn, 1, "Ascent", "atk", "Alpha")
    _add_round(conn, 1, "Ascent", "def", "Beta")
    # A round on a map Alpha did not play (different match) is excluded.
    _add_match(conn, 2, 200, 300, 2, 0, "2024-02-10")
    _add_map_result(conn, 2, "Bind", "Beta", "Gamma", "Beta")
    _add_round(conn, 2, "Bind", "atk", "Beta")
    conn.commit()

    rows = team_rounds(conn, 100)
    assert len(rows) == 2
    assert {r["map_name"] for r in rows} == {"Ascent"}

    w = DateWindow(date(2024, 2, 1), None)
    assert team_rounds(conn, 100, w) == []
    conn.close()


def test_team_detail_queries_empty_for_unknown_team(tmp_path):
    from valtrack.queries import team_map_results, team_rounds

    conn = _fresh_conn(tmp_path)
    assert team_map_results(conn, 999) == []
    assert team_rounds(conn, 999) == []
    conn.close()


def test_match_date_bounds(tmp_path):
    from valtrack.queries import match_date_bounds

    conn = _fresh_conn(tmp_path)
    assert match_date_bounds(conn) == (None, None)
    _add_match(conn, 1, 100, 200, 2, 0, "2024-03-01")
    _add_match(conn, 2, 100, 200, 2, 0, "2022-06-15")
    conn.commit()
    assert match_date_bounds(conn) == ("2022-06-15", "2024-03-01")
    conn.close()
