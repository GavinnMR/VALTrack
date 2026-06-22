"""Tests for the pure derivations: form and streak, and roster classification.

These are the bits that can silently mislead, so they are pinned to known
inputs: a streak must count the full current run, and the roster split must
survive VLR's unreliable staff flag and its occasionally mangled role text.
"""
from valtrack.stats import (
    align_rosters,
    classify_roster,
    form_and_streak,
    map_winrates,
    opening_duels,
    per_map_splits,
    pistol_winrate,
    player_aggregates,
    primary_role,
    side_winrates,
)


def test_form_and_streak_basic():
    res = form_and_streak(["W", "W", "L", "W", "L"])  # newest first
    assert res["form"] == ["W", "W", "L", "W", "L"]
    assert res["streak_kind"] == "W"
    assert res["streak_len"] == 2
    assert res["decided"] == 5


def test_streak_counts_full_run_past_the_form_window():
    res = form_and_streak(["L"] * 8, n=5)
    assert res["form"] == ["L"] * 5
    assert res["streak_kind"] == "L"
    assert res["streak_len"] == 8
    assert res["decided"] == 8


def test_form_trims_to_n():
    res = form_and_streak(["W", "L", "W", "L", "W", "L", "W"], n=3)
    assert res["form"] == ["W", "L", "W"]


def test_alternating_streak_is_one():
    res = form_and_streak(["W", "L", "W"])
    assert res["streak_kind"] == "W"
    assert res["streak_len"] == 1


def test_empty_results():
    res = form_and_streak([])
    assert res["form"] == []
    assert res["streak_kind"] is None
    assert res["streak_len"] == 0
    assert res["decided"] == 0


def _row(alias, role="", is_captain=0, real_name=""):
    return {
        "alias": alias,
        "real_name": real_name,
        "role": role,
        "is_captain": is_captain,
    }


def test_classify_blank_role_is_a_main_player():
    out = classify_roster([_row("a"), _row("b")])
    assert [m["alias"] for m in out["mains"]] == ["a", "b"]
    assert out["subs"] == []
    assert out["staff"] == []


def test_classify_staff_by_role_text():
    rows = [
        _row("coachy", role="head coach"),
        _row("asst", role="assistant coach"),
        _row("mgr", role="manager"),
    ]
    out = classify_roster(rows)
    assert out["mains"] == []
    assert [s["alias"] for s in out["staff"]] == ["coachy", "asst", "mgr"]


def test_classify_standin_even_with_mangled_text():
    # The source sometimes concatenates surname and role, e.g. "Wongstand-in".
    out = classify_roster([_row("Victor", role="Wongstand-in")])
    assert [s["alias"] for s in out["subs"]] == ["Victor"]


def test_classify_marks_captain():
    out = classify_roster([_row("cap", is_captain=1), _row("reg")])
    assert [m["alias"] for m in out["mains"] if m["is_captain"]] == ["cap"]


def test_classify_realistic_mix_yields_five_mains():
    rows = [
        _row("johnqt", is_captain=1),
        _row("Reduxx"),
        _row("Jerrwin"),
        _row("cortezia"),
        _row("JonahP"),
        _row("Victor", role="Wongstand-in"),
        _row("Zyto", role="manager"),
        _row("Ewok", role="head coach"),
        _row("GUNTER", role="assistant coach"),
    ]
    out = classify_roster(rows)
    assert len(out["mains"]) == 5
    assert len(out["subs"]) == 1
    assert len(out["staff"]) == 3


# --- per-map win rates and side splits (Build Step 6) -----------------------

def _map(map_name, winner, t1="A", t2="B"):
    return {
        "map_name": map_name,
        "winner_name": winner,
        "team1_name": t1,
        "team2_name": t2,
    }


def _rounds(map_name, spec):
    """Expand a compact spec into round rows.

    `spec` is a list of (winner_side, winner_team, count) tuples.
    """
    rows = []
    for side, team, count in spec:
        rows.extend(
            {"map_name": map_name, "winner_side": side, "winner_team": team}
            for _ in range(count)
        )
    return rows


def test_map_winrates_counts_decided_and_skips_forfeits():
    rows = [
        _map("Ascent", "A"),
        _map("Bind", "B"),
        _map("Haven", "A"),
        _map("Haven", "B"),
        _map("Split", None),  # forfeit / unplayed, no winner
    ]
    out = map_winrates(rows, "A")
    assert out["Ascent"] == {"won": 1, "lost": 0, "winrate": 1.0}
    assert out["Bind"] == {"won": 0, "lost": 1, "winrate": 0.0}
    assert out["Haven"] == {"won": 1, "lost": 1, "winrate": 0.5}
    # The forfeit is present but decides nothing, so its rate is None.
    assert out["Split"] == {"won": 0, "lost": 0, "winrate": None}


def test_side_winrates_hand_checked():
    # Ascent from A's point of view, a 13-3 win:
    #   A wins 4 attacking, B wins 2 defending  -> A attacked 6, won 4
    #   A wins 9 defending, B wins 1 attacking   -> A defended 10, won 9
    rounds = _rounds("Ascent", [
        ("atk", "A", 4),
        ("def", "B", 2),
        ("def", "A", 9),
        ("atk", "B", 1),
    ])
    out = side_winrates(rounds, "A")["Ascent"]
    assert out["atk_won"] == 4
    assert out["atk_total"] == 6
    assert out["atk_winrate"] == 4 / 6
    assert out["def_won"] == 9
    assert out["def_total"] == 10
    assert out["def_winrate"] == 9 / 10


def test_side_winrates_from_opponent_perspective_is_the_mirror():
    rounds = _rounds("Ascent", [
        ("atk", "A", 4),
        ("def", "B", 2),
        ("def", "A", 9),
        ("atk", "B", 1),
    ])
    out = side_winrates(rounds, "B")["Ascent"]
    # B attacked when A defended: B won 1, lost 9 -> 1/10 attacking.
    assert out["atk_won"] == 1
    assert out["atk_total"] == 10
    assert out["atk_winrate"] == 1 / 10
    # B defended when A attacked: B won 2, lost 4 -> 2/6 defending.
    assert out["def_won"] == 2
    assert out["def_total"] == 6
    assert out["def_winrate"] == 2 / 6


def test_side_winrate_is_none_when_a_side_has_no_rounds():
    # A swept on defense only (no attack rounds recorded for the map).
    rounds = _rounds("Icebox", [("def", "A", 13), ("atk", "B", 5)])
    out = side_winrates(rounds, "A")["Icebox"]
    assert out["atk_won"] == 0
    assert out["atk_total"] == 0
    assert out["atk_winrate"] is None
    assert out["def_won"] == 13
    assert out["def_total"] == 18
    assert out["def_winrate"] == 13 / 18


def test_per_map_splits_merges_and_orders_by_decided_maps():
    map_rows = [
        _map("Lotus", "A"),
        _map("Lotus", "A"),
        _map("Sunset", "B"),
    ]
    round_rows = (
        _rounds("Lotus", [("atk", "A", 13), ("def", "B", 5)])
        + _rounds("Sunset", [("def", "A", 7), ("atk", "B", 13)])
    )
    table = per_map_splits(map_rows, round_rows, "A")
    # Lotus has two decided maps, Sunset one, so Lotus sorts first.
    assert [r["map_name"] for r in table] == ["Lotus", "Sunset"]
    lotus = table[0]
    assert lotus["won"] == 2 and lotus["lost"] == 0
    assert lotus["map_winrate"] == 1.0
    # A won 13 attacking, B won 5 defending: A attacked all 18 rounds here.
    assert lotus["atk_won"] == 13 and lotus["atk_total"] == 18
    assert lotus["def_total"] == 0
    assert lotus["rounds_total"] == 18
    sunset = table[1]
    assert sunset["won"] == 0 and sunset["lost"] == 1
    # A won 7 defending, B won 13 attacking: A defended all 20 rounds here.
    assert sunset["def_won"] == 7 and sunset["def_total"] == 20
    assert sunset["atk_total"] == 0


# --- pistol-round win rate (Build Step 7) -----------------------------------

def _pistols(spec):
    """Expand a compact spec into pistol round rows.

    `spec` is a list of (winner_side, winner_team, count) tuples, all flagged as
    pistols. Real rounds carry a map_name too, but pistol_winrate ignores it, so
    these rows only need what it reads.
    """
    rows = []
    for side, team, count in spec:
        rows.extend(
            {"winner_side": side, "winner_team": team, "is_pistol": 1}
            for _ in range(count)
        )
    return rows


def test_pistol_winrate_hand_checked_overall_and_sides():
    # A's pistols across some maps:
    #   won 3 on attack, lost 2 on attack (opponent won those defending),
    #   won 4 on defense, lost 1 on defense (opponent won that attacking).
    rows = _pistols([
        ("atk", "A", 3),  # A attacked and won
        ("def", "B", 2),  # B defended and won -> A attacked and lost
        ("def", "A", 4),  # A defended and won
        ("atk", "B", 1),  # B attacked and won -> A defended and lost
    ])
    out = pistol_winrate(rows, "A")
    assert out["won"] == 7
    assert out["total"] == 10
    assert out["winrate"] == 7 / 10
    assert out["atk_won"] == 3 and out["atk_total"] == 5
    assert out["atk_winrate"] == 3 / 5
    assert out["def_won"] == 4 and out["def_total"] == 5
    assert out["def_winrate"] == 4 / 5


def test_pistol_winrate_from_opponent_is_the_mirror():
    rows = _pistols([
        ("atk", "A", 3),
        ("def", "B", 2),
        ("def", "A", 4),
        ("atk", "B", 1),
    ])
    out = pistol_winrate(rows, "B")
    # B won the 2 def pistols and the 1 atk pistol -> 3 of 10.
    assert out["won"] == 3
    assert out["total"] == 10
    assert out["winrate"] == 3 / 10
    # B attacked when A defended: B won 1, lost 4 -> 1 of 5.
    assert out["atk_won"] == 1 and out["atk_total"] == 5
    # B defended when A attacked: B won 2, lost 3 -> 2 of 5.
    assert out["def_won"] == 2 and out["def_total"] == 5


def test_pistol_winrate_ignores_non_pistol_rounds():
    rows = _pistols([("atk", "A", 1), ("def", "A", 1)])
    # Non-pistol rounds on the same maps must not count toward pistols.
    rows += [
        {"winner_side": "atk", "winner_team": "A", "is_pistol": 0},
        {"winner_side": "def", "winner_team": "B", "is_pistol": 0},
    ]
    out = pistol_winrate(rows, "A")
    assert out["won"] == 2
    assert out["total"] == 2
    assert out["winrate"] == 1.0


def test_pistol_winrate_is_none_with_no_pistols():
    out = pistol_winrate([], "A")
    assert out["won"] == 0
    assert out["total"] == 0
    assert out["winrate"] is None
    assert out["atk_winrate"] is None
    assert out["def_winrate"] is None


# --- opening-duel win rate (Build Step 8) -----------------------------------

def _duel(team, player, fk, fd, atk_fk, atk_fd, def_fk, def_fd):
    """One per-map opening-duel row, shaped like a map_player_stats row."""
    return {
        "team_name": team,
        "player_name": player,
        "first_kills": fk,
        "first_deaths": fd,
        "first_kills_atk": atk_fk,
        "first_deaths_atk": atk_fd,
        "first_kills_def": def_fk,
        "first_deaths_def": def_fd,
    }


def test_opening_duels_hand_checked_team_and_sides():
    rows = [
        # Two maps for two of A's players, plus an opponent row that must not
        # count toward A's totals.
        _duel("A", "ace", 4, 2, 3, 1, 1, 1),
        _duel("A", "ace", 3, 3, 2, 1, 1, 2),
        _duel("A", "bee", 1, 4, 0, 2, 1, 2),
        _duel("B", "zee", 9, 0, 5, 0, 4, 0),
    ]
    out = opening_duels(rows, "A")
    # Team totals: fk 4+3+1 = 8, fd 2+3+4 = 9, duels 17.
    assert out["fk"] == 8 and out["fd"] == 9 and out["duels"] == 17
    assert out["winrate"] == 8 / 17
    # Attack: fk 3+2+0 = 5, fd 1+1+2 = 4.
    assert out["atk_fk"] == 5 and out["atk_duels"] == 9
    assert out["atk_winrate"] == 5 / 9
    # Defense: fk 1+1+1 = 3, fd 1+2+2 = 5.
    assert out["def_fk"] == 3 and out["def_duels"] == 8
    assert out["def_winrate"] == 3 / 8


def test_opening_duels_per_player_aggregates_and_sorts_by_duels():
    rows = [
        _duel("A", "ace", 4, 2, 3, 1, 1, 1),
        _duel("A", "ace", 3, 3, 2, 1, 1, 2),
        _duel("A", "bee", 1, 4, 0, 2, 1, 2),
    ]
    out = opening_duels(rows, "A")
    # ace has 12 duels, bee 5, so ace leads.
    assert [p["player_name"] for p in out["players"]] == ["ace", "bee"]
    ace = out["players"][0]
    assert ace["fk"] == 7 and ace["fd"] == 5 and ace["duels"] == 12
    assert ace["winrate"] == 7 / 12
    assert ace["atk_fk"] == 5 and ace["atk_duels"] == 7
    assert ace["def_fk"] == 2 and ace["def_duels"] == 5


def test_opening_duels_ignores_the_opponent():
    rows = [
        _duel("A", "ace", 4, 2, 3, 1, 1, 1),
        _duel("B", "zee", 9, 9, 5, 5, 4, 4),
    ]
    out = opening_duels(rows, "A")
    assert out["fk"] == 4 and out["fd"] == 2
    assert [p["player_name"] for p in out["players"]] == ["ace"]


def test_opening_duels_treats_null_counts_as_zero():
    rows = [
        _duel("A", "ace", None, None, None, None, None, None),
        _duel("A", "ace", 2, 1, 2, 1, None, None),
    ]
    out = opening_duels(rows, "A")
    assert out["fk"] == 2 and out["fd"] == 1 and out["duels"] == 3
    assert out["atk_fk"] == 2 and out["atk_duels"] == 3
    # No defense duels recorded, so that side stays None rather than 0%.
    assert out["def_duels"] == 0 and out["def_winrate"] is None


def test_opening_duels_is_none_with_no_duels():
    out = opening_duels([], "A")
    assert out["duels"] == 0
    assert out["winrate"] is None
    assert out["atk_winrate"] is None
    assert out["def_winrate"] is None
    assert out["players"] == []


# --- aggregated player statistics (Build Step 9) ----------------------------

def _pstat(team, player, rounds, agent="Jett", rating=1.0, acs=200.0,
           kills=0, deaths=0, assists=0, kast="70%", adr=140.0, hs_pct="20%",
           first_kills=0, first_deaths=0):
    """One per-map player line, shaped like a team_player_stats row."""
    return {
        "team_name": team,
        "player_name": player,
        "player_id": None,
        "agent": agent,
        "map_rounds": rounds,
        "rating": rating,
        "acs": acs,
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "kast": kast,
        "adr": adr,
        "hs_pct": hs_pct,
        "first_kills": first_kills,
        "first_deaths": first_deaths,
    }


def test_player_aggregates_counts_are_summed_then_divided():
    # ace plays two maps: a 20-round map and a 24-round map (44 rounds total).
    rows = [
        _pstat("A", "ace", 20, kills=18, deaths=12, assists=5,
               first_kills=6, first_deaths=3),
        _pstat("A", "ace", 24, kills=22, deaths=20, assists=7,
               first_kills=4, first_deaths=5),
    ]
    out = player_aggregates(rows, "A")
    assert len(out) == 1
    ace = out[0]
    assert ace["maps"] == 2
    assert ace["rounds"] == 44
    assert ace["kills"] == 40 and ace["deaths"] == 32 and ace["assists"] == 12
    # K/D over totals, not a mean of per-map ratios.
    assert ace["kd"] == 40 / 32
    assert ace["kpr"] == 40 / 44
    assert ace["apr"] == 12 / 44
    assert ace["fk_per_round"] == 10 / 44
    assert ace["fd_per_round"] == 8 / 44
    # Opening-duel win rate is first kills over opening duels (fk + fd).
    assert ace["open_duels"] == 18
    assert ace["open_winrate"] == 10 / 18


def test_player_aggregates_rate_stats_are_round_weighted():
    # Two maps with different round counts, so a simple mean would be wrong.
    rows = [
        _pstat("A", "ace", 10, rating=1.0, acs=100.0, adr=100.0,
               kast="60%", hs_pct="10%"),
        _pstat("A", "ace", 30, rating=2.0, acs=300.0, adr=200.0,
               kast="80%", hs_pct="30%"),
    ]
    out = player_aggregates(rows, "A")[0]
    # Weighted by 10 and 30 rounds: (x*10 + y*30) / 40.
    assert out["rating"] == (1.0 * 10 + 2.0 * 30) / 40
    assert out["acs"] == (100.0 * 10 + 300.0 * 30) / 40
    assert out["adr"] == (100.0 * 10 + 200.0 * 30) / 40
    # KAST and HS% are parsed from their "75%" text before weighting.
    assert out["kast"] == (60 * 10 + 80 * 30) / 40
    assert out["hs_pct"] == (10 * 10 + 30 * 30) / 40


def test_player_aggregates_skips_missing_value_and_unknown_rounds():
    rows = [
        # A blank rating must not pull the average toward zero.
        _pstat("A", "ace", 20, rating=None, acs=200.0),
        _pstat("A", "ace", 20, rating=1.5, acs=240.0),
        # A map with an unknown round count drops out of round-weighted stats
        # and out of the per-round denominators, but its kills still aggregate.
        _pstat("A", "ace", None, rating=3.0, acs=999.0, kills=5),
    ]
    out = player_aggregates(rows, "A")[0]
    # Rating averages only the one map that has a rating and a round count.
    assert out["rating"] == 1.5
    # ACS weights the two maps with known rounds equally (20 each).
    assert out["acs"] == (200.0 + 240.0) / 2
    # Rounds total ignores the unknown-round map; kills still count all three.
    assert out["rounds"] == 40
    assert out["kills"] == 5
    assert out["kpr"] == 5 / 40


def test_player_aggregates_agent_pool_and_per_agent():
    rows = [
        _pstat("A", "ace", 20, agent="Jett", rating=1.2, acs=240.0,
               kills=20, deaths=10),
        _pstat("A", "ace", 20, agent="Jett", rating=0.8, acs=160.0,
               kills=10, deaths=14),
        _pstat("A", "ace", 20, agent="Raze", rating=1.0, acs=200.0,
               kills=15, deaths=15),
    ]
    out = player_aggregates(rows, "A")[0]
    # Jett has two maps, Raze one, so Jett sorts first.
    assert [a["agent"] for a in out["agents"]] == ["Jett", "Raze"]
    jett = out["agents"][0]
    assert jett["maps"] == 2
    assert jett["kd"] == 30 / 24
    assert jett["rating"] == (1.2 + 0.8) / 2  # equal round weights
    assert jett["acs"] == (240.0 + 160.0) / 2
    raze = out["agents"][1]
    assert raze["maps"] == 1 and raze["kd"] == 15 / 15


def test_player_aggregates_ignores_opponent_and_sorts_by_maps():
    rows = [
        _pstat("A", "ace", 20),
        _pstat("A", "ace", 20),
        _pstat("A", "bee", 20),
        _pstat("B", "zee", 20),  # opponent, must not appear
    ]
    out = player_aggregates(rows, "A")
    assert [p["player_name"] for p in out] == ["ace", "bee"]  # ace has 2 maps
    assert out[0]["maps"] == 2 and out[1]["maps"] == 1


def test_player_aggregates_none_when_nothing_to_divide():
    # A single map with zero deaths and an unknown round count: every derived
    # rate has an empty denominator, so each is None rather than a fake 0.
    rows = [_pstat("A", "ace", None, rating=None, acs=None, kills=3, deaths=0)]
    out = player_aggregates(rows, "A")[0]
    assert out["kd"] is None
    assert out["kpr"] is None
    assert out["apr"] is None
    assert out["rating"] is None
    assert out["acs"] is None
    assert out["agents"] == [] or out["agents"][0]["rating"] is None


def test_player_aggregates_empty():
    assert player_aggregates([], "A") == []


# --- player versus player: role inference and alignment (Build Step 10) ------

def _agent(name, maps):
    return {"agent": name, "maps": maps}


def _player(name, *agent_pairs):
    """A minimal player_aggregates-shaped dict: name and an agent pool."""
    return {"player_name": name, "agents": [_agent(a, m) for a, m in agent_pairs]}


def test_primary_role_picks_most_played():
    assert primary_role([_agent("Jett", 3), _agent("Sova", 1)]) == "duelist"
    assert primary_role([_agent("Omen", 4), _agent("Killjoy", 2)]) == "controller"


def test_primary_role_unknown_agent_tallies_as_unknown():
    # A known role wins only when it actually has more maps than the unmapped one.
    assert primary_role([_agent("Jett", 2), _agent("Mystery", 1)]) == "duelist"
    assert primary_role([_agent("Jett", 1), _agent("Mystery", 3)]) == "unknown"


def test_primary_role_empty_pool_is_unknown():
    assert primary_role([]) == "unknown"


def test_primary_role_tie_breaks_by_role_order():
    # duelist precedes initiator in ROLE_ORDER, so an equal split favors duelist.
    assert primary_role([_agent("Jett", 2), _agent("Sova", 2)]) == "duelist"


def test_align_rosters_pairs_within_role_in_order():
    a = [_player("aJett", ("Jett", 5)), _player("aSova", ("Sova", 5)),
         _player("aOmen", ("Omen", 5))]
    b = [_player("bRaze", ("Raze", 5)), _player("bBreach", ("Breach", 5)),
         _player("bViper", ("Viper", 5))]
    pairs = align_rosters(a, b)
    assert [p["role"] for p in pairs] == ["duelist", "initiator", "controller"]
    assert pairs[0]["a"]["player_name"] == "aJett"
    assert pairs[0]["b"]["player_name"] == "bRaze"


def test_align_rosters_uneven_counts_pad_with_none():
    a = [_player("aJett", ("Jett", 5)), _player("aRaze", ("Raze", 3))]  # two duelists
    b = [_player("bReyna", ("Reyna", 5))]                                # one duelist
    pairs = align_rosters(a, b)
    assert [p["role"] for p in pairs] == ["duelist", "duelist"]
    assert pairs[0]["b"]["player_name"] == "bReyna"
    # The extra duelist on A pairs against nobody.
    assert pairs[1]["a"]["player_name"] == "aRaze" and pairs[1]["b"] is None


def test_align_rosters_unknown_last_and_empty_roles_skipped():
    a = [_player("aMystery", ("Mystery", 4))]  # unknown role
    b = [_player("bJett", ("Jett", 5))]         # duelist
    pairs = align_rosters(a, b)
    # Only the two roles present appear; unknown sorts after duelist.
    assert [p["role"] for p in pairs] == ["duelist", "unknown"]
    assert pairs[0]["a"] is None and pairs[0]["b"]["player_name"] == "bJett"
    assert pairs[1]["a"]["player_name"] == "aMystery" and pairs[1]["b"] is None


def test_align_rosters_preserves_input_order_within_role():
    # player_aggregates feeds players sorted by maps; alignment keeps that order.
    a = [_player("first", ("Jett", 9)), _player("second", ("Raze", 2))]
    pairs = align_rosters(a, [])
    assert [p["a"]["player_name"] for p in pairs] == ["first", "second"]


def test_align_rosters_empty_both_sides():
    assert align_rosters([], []) == []
