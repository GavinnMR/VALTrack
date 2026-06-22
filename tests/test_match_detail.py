"""Tests for the per-match detail parse and store.

These lock in the calculations that quietly corrupt later comparisons if wrong:
the t/ct to attack/defense mapping, pistol-round flagging, the attack and
defense round splits from VLR's half totals, veto parsing, and that storing a
match twice never duplicates rows.
"""
from valtrack import db
from valtrack.cleaning import (
    is_pistol_round,
    parse_float,
    parse_vetos,
    side_to_phase,
)
from valtrack.match_detail import parse_match_detail, store_match_detail


# A small fixture modeled on a real vlrggapi match detail segment: two maps,
# both teams' players, rounds that include the two pistols and an overtime
# round, and a veto string.
def _segment():
    return {
        "match_id": "9001",
        "event": {"name": "Test Event: Week 1", "series": "Group Stage"},
        "map_vetos": "ALP ban Split; BRV ban Lotus; ALP pick Haven; "
                     "BRV pick Bind; Ascent remains",
        "teams": [
            {"id": "1", "name": "Alpha", "score": "2"},
            {"id": "2", "name": "Bravo", "score": "0"},
        ],
        "maps": [
            {
                "map_name": "Haven",
                "score": {"team1": 13, "team2": 7},
                "score_ct": {"team1": "7", "team2": "4"},
                "score_t": {"team1": "6", "team2": "3"},
                "score_ot": {"team1": "", "team2": ""},
                "players": {
                    "team1": [{
                        "name": "Aplayer", "agent": "Jett", "rating": "1.20",
                        "acs": "250", "kills": "20", "deaths": "14",
                        "assists": "5", "kast": "75%", "adr": "165.2",
                        "hs_pct": "28%", "fk": "4", "fd": "2",
                        "fk_t": "3", "fk_ct": "1", "fd_t": "1", "fd_ct": "1",
                    }],
                    "team2": [{
                        "name": "Bplayer", "agent": "Sova", "rating": "0.95",
                        "acs": "180", "kills": "13", "deaths": "18",
                        "assists": "7", "kast": "60%", "adr": "120.0",
                        "hs_pct": "22%", "fk": "2", "fd": "5",
                        "fk_t": "1", "fk_ct": "1", "fd_t": "2", "fd_ct": "3",
                    }],
                },
                "rounds": [
                    {"round_num": 1, "winner": "team1", "side": "t"},
                    {"round_num": 2, "winner": "team2", "side": "ct"},
                    {"round_num": 13, "winner": "team1", "side": "ct"},
                    {"round_num": 25, "winner": "team2", "side": "t"},
                ],
            },
            {
                "map_name": "Bind",
                "score": {"team1": 11, "team2": 13},
                "score_ct": {"team1": "6", "team2": "7"},
                "score_t": {"team1": "5", "team2": "6"},
                "score_ot": {"team1": "", "team2": ""},
                "players": {
                    "team1": [{
                        "name": "Aplayer", "agent": "Raze", "rating": "1.05",
                        "acs": "210", "kills": "17", "deaths": "16",
                        "assists": "4", "kast": "68%", "adr": "140.0",
                        "hs_pct": "25%", "fk": "3", "fd": "3",
                        "fk_t": "2", "fk_ct": "1", "fd_t": "1", "fd_ct": "2",
                    }],
                    "team2": [{
                        "name": "Bplayer", "agent": "Killjoy", "rating": "1.30",
                        "acs": "275", "kills": "22", "deaths": "13",
                        "assists": "6", "kast": "80%", "adr": "180.0",
                        "hs_pct": "30%", "fk": "5", "fd": "2",
                        "fk_t": "3", "fk_ct": "2", "fd_t": "1", "fd_ct": "1",
                    }],
                },
                "rounds": [
                    {"round_num": 1, "winner": "team2", "side": "ct"},
                    {"round_num": 2, "winner": "team1", "side": "t"},
                    {"round_num": 13, "winner": "team1", "side": "t"},
                    {"round_num": 14, "winner": "team2", "side": "ct"},
                ],
            },
        ],
    }


# --- cleaning helpers -------------------------------------------------------

def test_side_to_phase_maps_t_and_ct():
    assert side_to_phase("t") == "atk"
    assert side_to_phase("ct") == "def"
    assert side_to_phase("") is None
    assert side_to_phase(None) is None


def test_is_pistol_round_only_first_of_each_half():
    assert is_pistol_round(1)
    assert is_pistol_round(13)
    for n in (2, 12, 14, 24, 25, 26):
        assert not is_pistol_round(n)


def test_parse_float_pulls_numbers_and_keeps_blanks_none():
    assert parse_float("1.32") == 1.32
    assert parse_float("267") == 267.0
    assert parse_float("172.3") == 172.3
    assert parse_float("") is None
    assert parse_float(None) is None


def test_parse_vetos_orders_and_classifies():
    vetos = parse_vetos(
        "ALP ban Split; BRV ban Lotus; ALP pick Haven; BRV pick Bind; "
        "Ascent remains"
    )
    assert [v["seq"] for v in vetos] == [1, 2, 3, 4, 5]
    assert vetos[0] == {"seq": 1, "team_token": "ALP", "action": "ban", "map_name": "Split"}
    assert vetos[2]["action"] == "pick" and vetos[2]["map_name"] == "Haven"
    # The decider has no team and is the leftover map.
    assert vetos[4] == {"seq": 5, "team_token": None, "action": "remains", "map_name": "Ascent"}


def test_parse_vetos_empty_input():
    assert parse_vetos("") == []
    assert parse_vetos(None) == []


# --- parse ------------------------------------------------------------------

def test_parse_match_detail_shape():
    parsed = parse_match_detail(_segment())
    assert parsed["event_name"] == "Test Event: Week 1"
    assert len(parsed["vetos"]) == 5
    assert len(parsed["maps"]) == 2


def test_parse_map_scores_and_side_splits():
    haven = parse_match_detail(_segment())["maps"][0]
    assert haven["map_name"] == "Haven"
    assert haven["map_order"] == 1
    assert haven["team1_score"] == 13 and haven["team2_score"] == 7
    assert haven["winner_name"] == "Alpha"
    # Attack rounds come from the T column, defense from the CT column.
    assert haven["team1_atk_rounds"] == 6 and haven["team1_def_rounds"] == 7
    assert haven["team2_atk_rounds"] == 3 and haven["team2_def_rounds"] == 4


def test_parse_rounds_side_winner_and_pistols():
    haven = parse_match_detail(_segment())["maps"][0]
    rounds = {r["round_number"]: r for r in haven["rounds"]}
    assert rounds[1]["winner_side"] == "atk"
    assert rounds[1]["winner_team"] == "Alpha"
    assert rounds[1]["is_pistol"] == 1
    assert rounds[2]["winner_side"] == "def" and rounds[2]["winner_team"] == "Bravo"
    assert rounds[2]["is_pistol"] == 0
    assert rounds[13]["winner_side"] == "def" and rounds[13]["is_pistol"] == 1
    # The overtime round is not a pistol even though it opens with low economy.
    assert rounds[25]["is_pistol"] == 0
    assert all(r["win_type"] is None for r in haven["rounds"])


def test_parse_rounds_skips_winnerless_phantom_columns():
    # VLR pads the round grid to 24 columns; trailing empty columns have no
    # winner and must not be stored as rounds.
    seg = _segment()
    seg["maps"][0]["rounds"].extend([
        {"round_num": 26, "winner": "", "side": ""},
        {"round_num": 27, "winner": None, "side": ""},
    ])
    haven = parse_match_detail(seg)["maps"][0]
    numbers = [r["round_number"] for r in haven["rounds"]]
    assert 26 not in numbers and 27 not in numbers
    assert numbers == [1, 2, 13, 25]


def test_parse_players_assigned_to_their_team():
    haven = parse_match_detail(_segment())["maps"][0]
    by_name = {p["player_name"]: p for p in haven["players"]}
    assert by_name["Aplayer"]["team_name"] == "Alpha"
    assert by_name["Bplayer"]["team_name"] == "Bravo"
    assert by_name["Aplayer"]["rating"] == 1.20
    assert by_name["Aplayer"]["first_kills"] == 4
    assert by_name["Aplayer"]["kast"] == "75%"


def test_parse_players_maps_per_side_opening_duels():
    # fk_t / fd_t are the attack totals, fk_ct / fd_ct the defense totals.
    haven = parse_match_detail(_segment())["maps"][0]
    a = {p["player_name"]: p for p in haven["players"]}["Aplayer"]
    assert a["first_kills_atk"] == 3 and a["first_kills_def"] == 1
    assert a["first_deaths_atk"] == 1 and a["first_deaths_def"] == 1
    # The combined totals still split into the two sides.
    assert a["first_kills_atk"] + a["first_kills_def"] == a["first_kills"]
    assert a["first_deaths_atk"] + a["first_deaths_def"] == a["first_deaths"]


# --- store ------------------------------------------------------------------

def _conn_with_match(tmp_path):
    path = tmp_path / "t.db"
    db.init_db(path)
    conn = db.connect(path)
    conn.execute(
        "INSERT INTO matches (match_id, team1_name, team2_name, team1_score, "
        "team2_score) VALUES (9001, 'Alpha', 'Bravo', 2, 0)"
    )
    # A roster player so id resolution can be exercised; Bplayer is unknown.
    conn.execute("INSERT INTO players (id, alias) VALUES (99, 'Aplayer')")
    conn.commit()
    return conn


def test_store_writes_all_rich_tables(tmp_path):
    conn = _conn_with_match(tmp_path)
    parsed = parse_match_detail(_segment())
    store_match_detail(conn, 9001, parsed)
    conn.commit()

    def count(table):
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    assert count("map_results") == 2
    assert count("map_player_stats") == 4
    assert count("rounds") == 8
    assert count("match_vetos") == 5

    row = conn.execute(
        "SELECT event_name, map_vetos_raw, details_fetched_at FROM matches "
        "WHERE match_id = 9001"
    ).fetchone()
    assert row["event_name"] == "Test Event: Week 1"
    assert "Ascent remains" in row["map_vetos_raw"]
    assert row["details_fetched_at"] is not None
    conn.close()


def test_store_writes_per_side_opening_duels(tmp_path):
    conn = _conn_with_match(tmp_path)
    store_match_detail(conn, 9001, parse_match_detail(_segment()))
    conn.commit()
    # Aplayer across both maps: attack FK 3+2, defense FK 1+1.
    row = conn.execute(
        "SELECT SUM(first_kills_atk) AS atk_fk, SUM(first_kills_def) AS def_fk, "
        "SUM(first_deaths_atk) AS atk_fd, SUM(first_deaths_def) AS def_fd "
        "FROM map_player_stats WHERE player_name = 'Aplayer'"
    ).fetchone()
    assert (row["atk_fk"], row["def_fk"]) == (5, 2)
    assert (row["atk_fd"], row["def_fd"]) == (2, 3)
    conn.close()


def test_store_resolves_known_player_id_and_leaves_unknown_null(tmp_path):
    conn = _conn_with_match(tmp_path)
    store_match_detail(conn, 9001, parse_match_detail(_segment()))
    conn.commit()

    aplayer_ids = conn.execute(
        "SELECT DISTINCT player_id FROM map_player_stats WHERE player_name = 'Aplayer'"
    ).fetchall()
    assert [r["player_id"] for r in aplayer_ids] == [99]
    bplayer_id = conn.execute(
        "SELECT DISTINCT player_id FROM map_player_stats WHERE player_name = 'Bplayer'"
    ).fetchone()
    assert bplayer_id["player_id"] is None
    conn.close()


def test_store_is_idempotent(tmp_path):
    conn = _conn_with_match(tmp_path)
    parsed = parse_match_detail(_segment())
    store_match_detail(conn, 9001, parsed)
    conn.commit()
    store_match_detail(conn, 9001, parsed)  # re-run must not duplicate
    conn.commit()

    def count(table):
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    assert count("map_results") == 2
    assert count("map_player_stats") == 4
    assert count("rounds") == 8
    assert count("match_vetos") == 5
    conn.close()
