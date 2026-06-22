"""Tests for the read-side queries, focused on the overall record computation.

team_record is the one bit of aggregation in this step. The tricky part is that
a franchise team can be stored in either match slot, so the record must hold up
whether the team is team1 or team2, and must ignore matches that are not decided.
"""
from valtrack import db


def _fresh_conn(tmp_path):
    path = tmp_path / "t.db"
    db.init_db(path)
    return db.connect(path)


def _add_match(conn, match_id, t1_id, t2_id, t1_score, t2_score):
    conn.execute(
        """
        INSERT INTO matches (match_id, team1_id, team2_id, team1_score, team2_score)
        VALUES (?, ?, ?, ?, ?)
        """,
        (match_id, t1_id, t2_id, t1_score, t2_score),
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
