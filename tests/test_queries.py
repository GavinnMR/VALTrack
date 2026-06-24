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


def _seed_detail_match(conn, team_name="Alpha", opp_name="Beta"):
    """A team, a dated match, a map result, and two player rows, for detail reads."""
    conn.execute(
        "INSERT INTO teams (id, name, tag, regional_rank) VALUES (1, ?, 'ALP', 5)",
        (team_name,),
    )
    conn.execute(
        "INSERT INTO teams (id, name, tag, regional_rank) VALUES (2, ?, 'BET', 9)",
        (opp_name,),
    )
    conn.execute(
        "INSERT INTO matches (match_id, team1_id, team1_name, team2_id, team2_name, "
        "team1_score, team2_score, date) VALUES (1, 1, ?, 2, ?, 1, 0, '2026-05-01')",
        (team_name, opp_name),
    )
    conn.execute(
        "INSERT INTO map_results (match_id, map_name, map_order, team1_name, "
        "team2_name, team1_score, team2_score, winner_name) "
        "VALUES (1, 'Lotus', 1, ?, ?, 13, 7, ?)",
        (team_name, opp_name, team_name),
    )
    for name, agent in (("a1", "Jett"), ("a2", "Omen")):
        conn.execute(
            "INSERT INTO map_player_stats (match_id, map_name, player_name, "
            "team_name, agent, clutch_won, clutch_lost) "
            "VALUES (1, 'Lotus', ?, ?, ?, 2, 1)",
            (name, team_name, agent),
        )
    conn.commit()


def test_team_compositions_reads_agents_and_winner(tmp_path):
    from valtrack.queries import team_compositions

    conn = _fresh_conn(tmp_path)
    _seed_detail_match(conn)
    rows = team_compositions(conn, 1)
    agents = sorted(r["agent"] for r in rows)
    assert agents == ["Jett", "Omen"]
    assert all(r["winner_name"] == "Alpha" and r["map_name"] == "Lotus" for r in rows)
    conn.close()


def test_team_performance_reads_counts(tmp_path):
    from valtrack.queries import team_performance
    from valtrack.stats import clutch_stats, multikill_stats, utility_stats

    conn = _fresh_conn(tmp_path)
    _seed_detail_match(conn)
    for name in ("a1", "a2"):
        conn.execute(
            "INSERT INTO match_player_perf (match_id, player_name, team_name, "
            "mk_2k, mk_3k, mk_4k, mk_5k, clutch_1v1, clutch_1v2, clutch_1v3, "
            "clutch_1v4, clutch_1v5, plants, defuses) "
            "VALUES (1, ?, 'Alpha', 5, 1, 0, 0, 2, 1, 0, 0, 0, 4, 1)",
            (name,),
        )
    conn.commit()
    rows = team_performance(conn, 1)
    # Two players, each 3 clutch wins (2 1v1 + 1 1v2) over the one match.
    assert clutch_stats(rows, "Alpha")["won"] == 6
    assert utility_stats(rows, "Alpha")["plants"] == 8
    assert sum(p["total"] for p in multikill_stats(rows, "Alpha")) == 12
    conn.close()


def test_team_round_win_types_counts_by_side(tmp_path):
    from valtrack.queries import team_round_win_types

    conn = _fresh_conn(tmp_path)
    _seed_detail_match(conn)
    for side, wt in (("def", "defuse"), ("def", "elim"), ("atk", "boom"),
                     ("atk", None)):
        conn.execute(
            "INSERT INTO rounds (match_id, map_name, round_number, winner_side, "
            "winner_team, win_type) VALUES (1, 'Lotus', 1, ?, 'Alpha', ?)",
            (side, wt),
        )
    conn.commit()
    got = {(r["winner_side"], r["win_type"]): r["n"]
           for r in team_round_win_types(conn, 1)}
    # The null-win-type row is excluded; the rest are grouped by side and type.
    assert got == {("def", "defuse"): 1, ("def", "elim"): 1, ("atk", "boom"): 1}
    conn.close()


def test_team_map_opponent_rank_averages_opponent_rank(tmp_path):
    from valtrack.queries import team_map_opponent_rank

    conn = _fresh_conn(tmp_path)
    _seed_detail_match(conn)
    rows = {r["map_name"]: r for r in team_map_opponent_rank(conn, 1)}
    # The one Lotus map was against Beta, whose stored regional rank is 9.
    assert rows["Lotus"]["avg_rank"] == 9 and rows["Lotus"]["ranked"] == 1
    conn.close()


def test_team_economy_empty_without_economy_rows(tmp_path):
    from valtrack.queries import team_economy

    conn = _fresh_conn(tmp_path)
    _seed_detail_match(conn)
    # No map_economy rows seeded, so the read is honestly empty.
    assert team_economy(conn, 1) == []
    conn.close()


def test_team_economy_reads_aggregate_rows(tmp_path):
    from valtrack.queries import team_economy
    from valtrack.stats import economy_conversion

    conn = _fresh_conn(tmp_path)
    _seed_detail_match(conn)
    for buy, played, won in (("eco", 5, 2), ("full", 10, 8)):
        conn.execute(
            "INSERT INTO map_economy (match_id, map_name, team_name, buy_type, "
            "played, won) VALUES (1, 'Lotus', 'Alpha', ?, ?, ?)",
            (buy, played, won),
        )
    conn.commit()
    out = economy_conversion(team_economy(conn, 1), "Alpha")
    assert out["eco"] == {"won": 2, "total": 5, "winrate": 0.4}
    assert out["full"]["winrate"] == 0.8
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


def _add_map_result_scored(conn, match_id, map_name, t1_name, t2_name,
                           t1_score, t2_score):
    conn.execute(
        """
        INSERT INTO map_results (match_id, map_name, team1_name, team2_name,
                                 team1_score, team2_score, winner_name)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (match_id, map_name, t1_name, t2_name, t1_score, t2_score,
         t1_name if t1_score > t2_score else t2_name),
    )


def _add_player_stat(conn, match_id, map_name, player, team, agent="Jett",
                     kills=0, deaths=0):
    conn.execute(
        """
        INSERT INTO map_player_stats (match_id, map_name, player_name, team_name,
                                      agent, rating, acs, kills, deaths, assists,
                                      kast, adr, hs_pct, first_kills, first_deaths)
        VALUES (?, ?, ?, ?, ?, 1.0, 200, ?, ?, 3, '70%', 140, '20%', 2, 1)
        """,
        (match_id, map_name, player, team, agent, kills, deaths),
    )


def test_team_player_stats_joins_round_count_and_windows(tmp_path):
    from valtrack.queries import team_player_stats

    conn = _fresh_conn(tmp_path)
    _add_team(conn, 100, "Alpha")
    _add_match(conn, 1, 100, 200, 2, 0, "2024-01-10")
    _add_map_result_scored(conn, 1, "Ascent", "Alpha", "Beta", 13, 7)
    _add_player_stat(conn, 1, "Ascent", "ace", "Alpha", kills=18, deaths=10)
    _add_player_stat(conn, 1, "Ascent", "zee", "Beta", kills=12, deaths=16)
    # A later match outside the window for the same team.
    _add_match(conn, 2, 100, 200, 2, 1, "2024-09-10")
    _add_map_result_scored(conn, 2, "Bind", "Alpha", "Beta", 13, 11)
    _add_player_stat(conn, 2, "Bind", "ace", "Alpha", kills=20, deaths=18)
    conn.commit()

    rows = team_player_stats(conn, 100)
    # Only Alpha's players come back (Beta's row is excluded by team_name).
    assert sorted((r["player_name"], r["map_name"]) for r in rows) == [
        ("ace", "Ascent"), ("ace", "Bind"),
    ]
    ascent = next(r for r in rows if r["map_name"] == "Ascent")
    # The round count is both teams' scores summed from map_results.
    assert ascent["map_rounds"] == 20
    assert ascent["kills"] == 18

    w = DateWindow(date(2024, 1, 1), date(2024, 6, 30))
    windowed = team_player_stats(conn, 100, w)
    assert [r["map_name"] for r in windowed] == ["Ascent"]

    assert team_player_stats(conn, 999) == []
    conn.close()


def test_team_record_event_filter_excludes_unknown_events(tmp_path):
    from valtrack.queries import team_record
    from valtrack.window import EventFilter

    conn = _fresh_conn(tmp_path)
    me, opp = 100, 200

    def add(mid, score_us, score_them, event_name):
        conn.execute(
            "INSERT INTO matches (match_id, team1_id, team2_id, team1_score, "
            "team2_score, date, event_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (mid, me, opp, score_us, score_them, "2024-01-10", event_name),
        )

    add(1, 2, 0, "Champions Tour 2024: Masters Madrid")  # LAN win
    add(2, 2, 1, "VCT 2024: EMEA Stage 1")               # online win
    add(3, 0, 2, None)                                    # unknown-event loss
    conn.commit()

    assert team_record(conn, me) == {"wins": 2, "losses": 1, "decided": 3}
    lan = team_record(conn, me, events=EventFilter("lan"))
    assert lan == {"wins": 1, "losses": 0, "decided": 1}
    online = team_record(conn, me, events=EventFilter("online"))
    # The online win counts; the unknown-event loss is excluded from both buckets.
    assert online == {"wins": 1, "losses": 0, "decided": 1}
    conn.close()


def test_head_to_head(tmp_path):
    from valtrack.queries import head_to_head

    conn = _fresh_conn(tmp_path)
    me, opp = 100, 200
    _add_named_match(conn, 1, me, "Alpha", opp, "Beta", 2, 0, "2024-01-10")   # A win
    _add_named_match(conn, 2, opp, "Beta", me, "Alpha", 2, 1, "2024-02-10")   # B win, A is team2
    _add_named_match(conn, 3, me, "Alpha", 300, "Gamma", 2, 0, "2024-03-10")  # not h2h
    conn.commit()

    h = head_to_head(conn, me, opp)
    assert h["a_wins"] == 1 and h["b_wins"] == 1 and h["decided"] == 2
    # Newest first: match 2 (B win) then match 1 (A win).
    assert [m["winner"] for m in h["meetings"]] == ["b", "a"]
    # Scores are framed from A's point of view even when A was team2.
    assert h["meetings"][0]["a_score"] == 1 and h["meetings"][0]["b_score"] == 2


def test_schedule_strength(tmp_path):
    from valtrack.queries import schedule_strength

    conn = _fresh_conn(tmp_path)
    _add_team(conn, 100, "Alpha")
    _add_team(conn, 200, "Beta")
    _add_team(conn, 300, "Gamma")
    conn.execute("UPDATE teams SET regional_rank = 4 WHERE id = 200")
    conn.execute("UPDATE teams SET regional_rank = 10 WHERE id = 300")
    _add_match(conn, 1, 100, 200, 2, 0, "2024-01-10")   # opp rank 4
    _add_match(conn, 2, 100, 300, 2, 1, "2024-02-10")   # opp rank 10
    _add_match(conn, 3, 100, 999, 2, 0, "2024-03-10")   # opp not stored, no rank
    conn.commit()

    s = schedule_strength(conn, 100)
    assert s["decided"] == 3
    assert s["ranked"] == 2
    assert s["avg_opp_rank"] == 7.0   # (4 + 10) / 2


def test_common_opponents(tmp_path):
    from valtrack.queries import common_opponents

    conn = _fresh_conn(tmp_path)
    _add_team(conn, 100, "Alpha")
    _add_team(conn, 200, "Beta")
    # Alpha (100) and Beta (200) both played Gamma (300); only Alpha played Delta.
    _add_named_match(conn, 1, 100, "Alpha", 300, "Gamma", 2, 0, "2024-01-10")  # A beat Gamma
    _add_named_match(conn, 2, 300, "Gamma", 100, "Alpha", 2, 1, "2024-02-10")  # A lost to Gamma
    _add_named_match(conn, 3, 200, "Beta", 300, "Gamma", 0, 2, "2024-03-10")   # B lost to Gamma
    _add_named_match(conn, 4, 100, "Alpha", 400, "Delta", 2, 0, "2024-04-10")  # only Alpha
    # Alpha also played Beta directly; the other selected team is not a common opp.
    _add_named_match(conn, 5, 100, "Alpha", 200, "Beta", 2, 0, "2024-05-10")
    conn.commit()

    common = common_opponents(conn, 100, 200)
    assert [c["opponent"] for c in common] == ["Gamma"]  # Delta and Beta excluded
    gamma = common[0]
    assert gamma["a"] == {"wins": 1, "losses": 1}  # Alpha 1-1 vs Gamma
    assert gamma["b"] == {"wins": 0, "losses": 1}  # Beta 0-1 vs Gamma
    conn.close()


def test_last_match_date(tmp_path):
    from valtrack.queries import last_match_date

    conn = _fresh_conn(tmp_path)
    _add_match(conn, 1, 100, 200, 2, 0, "2024-03-01")
    _add_match(conn, 2, 200, 100, 2, 1, "2024-06-15")  # 100 in team2 slot
    conn.commit()
    assert last_match_date(conn, 100) == "2024-06-15"
    assert last_match_date(conn, 999) is None
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


def _add_ordered_map(conn, match_id, order, map_name, t1_name, t2_name, winner):
    conn.execute(
        """
        INSERT INTO map_results (match_id, map_order, map_name,
                                 team1_name, team2_name, winner_name)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (match_id, order, map_name, t1_name, t2_name, winner),
    )


def test_team_series_results_frames_series_score_by_slot(tmp_path):
    from valtrack.queries import team_series_results

    conn = _fresh_conn(tmp_path)
    _add_team(conn, 100, "Alpha")
    _add_team(conn, 200, "Beta")
    # Alpha is team1 here, won the series 2-1.
    _add_match(conn, 1, 100, 200, 2, 1, "2024-01-10")
    _add_ordered_map(conn, 1, 1, "Ascent", "Alpha", "Beta", "Alpha")
    _add_ordered_map(conn, 1, 2, "Bind", "Alpha", "Beta", "Beta")
    _add_ordered_map(conn, 1, 3, "Lotus", "Alpha", "Beta", "Alpha")
    # Alpha is team2 here, lost the series 1-2; score must still frame from Alpha.
    _add_match(conn, 2, 200, 100, 2, 1, "2024-02-10")
    _add_ordered_map(conn, 2, 1, "Split", "Beta", "Alpha", "Beta")
    conn.commit()

    rows = team_series_results(conn, 100)
    by_match = {}
    for r in rows:
        by_match.setdefault(r["match_id"], r)
    # Match 1: Alpha is team1, so 2-1 from Alpha's view.
    assert by_match[1]["team_series_score"] == 2
    assert by_match[1]["opp_series_score"] == 1
    # Match 2: Alpha is team2 with score 1, opponent 2.
    assert by_match[2]["team_series_score"] == 1
    assert by_match[2]["opp_series_score"] == 2
    assert team_series_results(conn, 999) == []
    conn.close()


def test_team_series_results_windowed(tmp_path):
    from valtrack.queries import team_series_results

    conn = _fresh_conn(tmp_path)
    _add_team(conn, 100, "Alpha")
    _add_match(conn, 1, 100, 200, 2, 0, "2024-01-10")
    _add_ordered_map(conn, 1, 1, "Ascent", "Alpha", "Beta", "Alpha")
    _add_match(conn, 2, 100, 200, 2, 0, "2024-09-10")
    _add_ordered_map(conn, 2, 1, "Bind", "Alpha", "Beta", "Alpha")
    conn.commit()
    w = DateWindow(date(2024, 1, 1), date(2024, 6, 30))
    rows = team_series_results(conn, 100, w)
    assert {r["match_id"] for r in rows} == {1}
    conn.close()


def test_meeting_maps_and_lineup(tmp_path):
    from valtrack.queries import meeting_lineup, meeting_maps

    conn = _fresh_conn(tmp_path)
    _add_match(conn, 1, 100, 200, 2, 0, "2024-01-10")
    _add_ordered_map(conn, 1, 2, "Bind", "Alpha", "Beta", "Alpha")
    _add_ordered_map(conn, 1, 1, "Ascent", "Alpha", "Beta", "Alpha")
    _add_player_stat(conn, 1, "Ascent", "ace", "Alpha")
    _add_player_stat(conn, 1, "Bind", "ace", "Alpha")   # same player, second map
    _add_player_stat(conn, 1, "Ascent", "bee", "Alpha")
    _add_player_stat(conn, 1, "Ascent", "zee", "Beta")
    conn.commit()

    maps = meeting_maps(conn, 1)
    # Ordered by map_order, so Ascent (1) before Bind (2).
    assert [m["map_name"] for m in maps] == ["Ascent", "Bind"]
    # Lineup is distinct players for the team, not one row per map.
    assert meeting_lineup(conn, 1, "Alpha") == ["ace", "bee"]
    assert meeting_lineup(conn, 1, "Beta") == ["zee"]
    # No detail stored is the honest empty state.
    assert meeting_maps(conn, 999) == []
    assert meeting_lineup(conn, 999, "Alpha") == []
    conn.close()


def test_detail_coverage(tmp_path):
    from valtrack.queries import detail_coverage

    conn = _fresh_conn(tmp_path)
    # Two matches in range, one detailed; one match out of range and detailed.
    conn.execute(
        "INSERT INTO matches (match_id, team1_id, team2_id, team1_score, "
        "team2_score, date, details_fetched_at) "
        "VALUES (1, 100, 200, 2, 0, '2024-03-01', '2024-03-02')"
    )
    conn.execute(
        "INSERT INTO matches (match_id, team1_id, team2_id, team1_score, "
        "team2_score, date) VALUES (2, 100, 200, 2, 1, '2024-03-05')"
    )
    conn.execute(
        "INSERT INTO matches (match_id, team1_id, team2_id, team1_score, "
        "team2_score, date, details_fetched_at) "
        "VALUES (3, 100, 200, 2, 0, '2023-01-01', '2023-01-02')"
    )
    conn.commit()
    w = DateWindow(date(2024, 1, 1), date(2024, 12, 31))
    cov = detail_coverage(conn, 100, w)
    assert cov == {"detailed": 1, "total": 2}
    # All time sees the older detailed match too.
    assert detail_coverage(conn, 100) == {"detailed": 2, "total": 3}
    conn.close()


def test_team_window_summary(tmp_path):
    from valtrack.queries import team_window_summary

    conn = _fresh_conn(tmp_path)
    _add_match(conn, 1, 100, 200, 2, 0, "2024-01-10")        # decided
    _add_match(conn, 2, 200, 100, 1, 1, "2024-05-10")        # tie, not decided
    _add_match(conn, 3, 100, 200, None, None, "2024-03-10")  # undecided
    conn.commit()
    s = team_window_summary(conn, 100)
    assert s["total"] == 3 and s["decided"] == 1
    assert s["min_date"] == "2024-01-10" and s["max_date"] == "2024-05-10"
    conn.close()


def test_team_record_stage_filter(tmp_path):
    from valtrack.queries import team_record
    from valtrack.window import StageFilter

    conn = _fresh_conn(tmp_path)
    me, opp = 100, 200
    _add_match(conn, 1, me, opp, 2, 0, "2024-01-10")   # group win
    _add_match(conn, 2, me, opp, 0, 2, "2024-02-10")   # playoff loss
    _add_match(conn, 3, me, opp, 2, 1, "2024-03-10")   # unclassified win
    conn.execute("UPDATE matches SET match_stage = 'group' WHERE match_id = 1")
    conn.execute("UPDATE matches SET match_stage = 'playoff' WHERE match_id = 2")
    conn.execute("UPDATE matches SET match_stage = 'unknown' WHERE match_id = 3")
    conn.commit()

    assert team_record(conn, me) == {"wins": 2, "losses": 1, "decided": 3}
    g = team_record(conn, me, stage=StageFilter("group"))
    assert g == {"wins": 1, "losses": 0, "decided": 1}
    p = team_record(conn, me, stage=StageFilter("playoff"))
    assert p == {"wins": 0, "losses": 1, "decided": 1}
    conn.close()


def test_team_rounds_carry_match_round_and_opp_rank(tmp_path):
    from valtrack.queries import team_rounds

    conn = _fresh_conn(tmp_path)
    _add_team(conn, 100, "Alpha")
    _add_team(conn, 200, "Beta")
    conn.execute("UPDATE teams SET regional_rank = 5 WHERE id = 200")
    _add_match(conn, 1, 100, 200, 1, 0, "2024-01-10")
    _add_map_result(conn, 1, "Ascent", "Alpha", "Beta", "Alpha")
    conn.execute(
        "INSERT INTO rounds (match_id, map_name, round_number, winner_side, "
        "winner_team, is_pistol) VALUES (1, 'Ascent', 1, 'atk', 'Alpha', 1)"
    )
    conn.commit()
    rows = team_rounds(conn, 100)
    assert rows[0]["match_id"] == 1 and rows[0]["round_number"] == 1
    # The opponent (Beta) has regional rank 5, surfaced for the by-tier split.
    assert rows[0]["opp_rank"] == 5
    conn.close()


def test_team_map_results_carry_scores_and_opp_rank(tmp_path):
    from valtrack.queries import team_map_results

    conn = _fresh_conn(tmp_path)
    _add_team(conn, 100, "Alpha")
    _add_team(conn, 200, "Beta")
    conn.execute("UPDATE teams SET regional_rank = 8 WHERE id = 200")
    _add_match(conn, 1, 100, 200, 1, 0, "2024-01-10")
    _add_map_result_scored(conn, 1, "Ascent", "Alpha", "Beta", 13, 7)
    conn.commit()
    row = team_map_results(conn, 100)[0]
    assert row["team1_score"] == 13 and row["team2_score"] == 7
    assert row["opp_rank"] == 8
    conn.close()


def test_head_to_head_maps(tmp_path):
    from valtrack.queries import head_to_head_maps

    conn = _fresh_conn(tmp_path)
    _add_team(conn, 100, "Alpha")
    _add_team(conn, 200, "Beta")
    # Two meetings; Bind played in both, Ascent once.
    _add_named_match(conn, 1, 100, "Alpha", 200, "Beta", 2, 1, "2024-01-10")
    _add_ordered_map(conn, 1, 1, "Bind", "Alpha", "Beta", "Alpha")
    _add_ordered_map(conn, 1, 2, "Ascent", "Alpha", "Beta", "Beta")
    _add_named_match(conn, 2, 200, "Beta", 100, "Alpha", 2, 0, "2024-05-10")
    _add_ordered_map(conn, 2, 1, "Bind", "Beta", "Alpha", "Beta")
    conn.commit()
    rows = {r["map_name"]: r for r in head_to_head_maps(conn, 100, 200)}
    # Bind: played twice, Alpha won one, Beta won one, last played 2024-05-10.
    assert rows["Bind"]["played"] == 2
    assert rows["Bind"]["a_wins"] == 1 and rows["Bind"]["b_wins"] == 1
    assert rows["Bind"]["last_date"] == "2024-05-10"
    assert rows["Ascent"]["played"] == 1 and rows["Ascent"]["b_wins"] == 1
    # No detail stored is the honest empty state.
    assert head_to_head_maps(conn, 100, 999) == []
    conn.close()


def test_team_rest_load(tmp_path):
    from valtrack.queries import team_rest_load

    conn = _fresh_conn(tmp_path)
    _add_team(conn, 100, "Alpha")
    _add_match(conn, 1, 100, 200, 2, 0, "2026-06-20")   # within 14 days of "today"
    _add_map_result(conn, 1, "Ascent", "Alpha", "Beta", "Alpha")
    _add_map_result(conn, 1, "Bind", "Alpha", "Beta", "Alpha")
    _add_match(conn, 2, 100, 200, 2, 1, "2026-06-01")   # within 30, not 14
    _add_map_result(conn, 2, "Lotus", "Alpha", "Beta", "Alpha")
    _add_match(conn, 3, 100, 200, 2, 0, "2026-01-01")   # long ago
    conn.commit()
    today = date(2026, 6, 23)
    out = team_rest_load(conn, 100, today=today)
    assert out["last_date"] == "2026-06-20" and out["days_since"] == 3
    assert out["matches_14"] == 1 and out["matches_30"] == 2
    assert out["maps_14"] == 2 and out["maps_30"] == 3
    conn.close()


def test_last_roster_change_date(tmp_path):
    from valtrack.queries import last_roster_change_date

    conn = _fresh_conn(tmp_path)
    _add_team(conn, 100, "Alpha")
    # a1 debuted long ago, a2 (a new starter) debuted recently.
    _add_match(conn, 1, 100, 200, 2, 0, "2024-01-10")
    _add_player_stat(conn, 1, "Ascent", "a1", "Alpha")
    _add_match(conn, 2, 100, 200, 2, 0, "2026-05-01")
    _add_player_stat(conn, 2, "Bind", "a1", "Alpha")
    _add_player_stat(conn, 2, "Bind", "a2", "Alpha")
    conn.commit()
    five = {"a1", "a2"}
    # The newest current starter (a2) debuted 2026-05-01, the approx last change.
    assert last_roster_change_date(conn, 100, five) == "2026-05-01"
    assert last_roster_change_date(conn, 100, set()) is None
    conn.close()
