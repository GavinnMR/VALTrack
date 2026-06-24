"""Tests for the pure derivations: form and streak, and roster classification.

These are the bits that can silently mislead, so they are pinned to known
inputs: a streak must count the full current run, and the roster split must
survive VLR's unreliable staff flag and its occasionally mangled role text.
"""
from valtrack.stats import (
    align_rosters,
    bands_overlap,
    calibration,
    canonical_player_name,
    classify_roster,
    clutch_stats,
    current_five_names,
    economy_conversion,
    field_summary,
    form_and_streak,
    infer_match_format,
    is_small_sample,
    keep_players,
    lineup_continuity,
    map_compositions,
    map_duel_board,
    map_pool_overlap,
    map_winrates,
    margin_profile,
    merge_player_aliases,
    multikill_stats,
    opening_duels,
    partition_by_tier,
    per_map_splits,
    percentile,
    pistol_winrate,
    player_aggregates,
    player_map_aggregates,
    post_pistol_conversion,
    pressure_stats,
    primary_role,
    rank_metric_gaps,
    round_win_conditions,
    side_winrates,
    team_rating,
    tier_of_rank,
    utility_stats,
    wilson_interval,
)


def test_is_small_sample():
    assert is_small_sample(3, 5) is True
    assert is_small_sample(5, 5) is False   # threshold itself is enough
    assert is_small_sample(9, 5) is False
    assert is_small_sample(0, 5) is True
    assert is_small_sample(None, 5) is True  # missing count counts as small


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


# --- roster validity: current-five names and filtering (Build Step 13) -------

def test_current_five_names_are_mains_casefolded():
    rows = [
        _row("Aspas"), _row("Less"), _row("pANcada", role="Wongstand-in"),
        _row("Coach", role="head coach"),
    ]
    names = current_five_names(rows)
    # Only the main players, lowercased; stand-in and staff excluded.
    assert names == {"aspas", "less"}


def test_keep_players_filters_by_name_set():
    rows = [
        {"player_name": "Aspas", "v": 1},
        {"player_name": "Less", "v": 2},
        {"player_name": "former", "v": 3},
    ]
    kept = keep_players(rows, {"aspas", "less"})
    assert [r["v"] for r in kept] == [1, 2]


def test_keep_players_none_passes_through():
    rows = [{"player_name": "x"}]
    assert keep_players(rows, None) is rows


# --- per-map player performance (item 7) ------------------------------------

def _pstat_map(map_name, **kw):
    """A per-map player line with its map name, for player_map_aggregates."""
    row = _pstat(kw.pop("team", "A"), kw.pop("player", "ace"),
                 kw.pop("rounds", 20), **kw)
    row["map_name"] = map_name
    return row


def test_player_map_aggregates_splits_a_player_by_map():
    rows = [
        _pstat_map("Ascent", player="ace", rounds=20, acs=300.0, kills=20, deaths=8),
        _pstat_map("Ascent", player="ace", rounds=20, acs=260.0, kills=18, deaths=10),
        _pstat_map("Lotus", player="ace", rounds=20, acs=120.0, kills=8, deaths=16),
        _pstat_map("Ascent", player="bee", rounds=20, acs=180.0),
    ]
    out = player_map_aggregates(rows, "A")
    assert set(out) == {"Ascent", "Lotus"}
    # On Ascent ace has two maps and leads bee; on Lotus only ace, much weaker.
    ascent_ace = next(p for p in out["Ascent"] if p["player_name"] == "ace")
    assert ascent_ace["maps"] == 2
    assert ascent_ace["acs"] == (300.0 + 260.0) / 2
    lotus_ace = out["Lotus"][0]
    assert lotus_ace["maps"] == 1 and lotus_ace["acs"] == 120.0


def test_player_map_aggregates_skips_rows_with_no_map_and_opponent():
    rows = [
        _pstat_map("Bind", player="ace", rounds=20),
        {**_pstat("A", "ace", 20), "map_name": None},   # no map, dropped
        _pstat_map("Bind", team="B", player="zee", rounds=20),  # opponent
    ]
    out = player_map_aggregates(rows, "A")
    assert set(out) == {"Bind"}
    assert [p["player_name"] for p in out["Bind"]] == ["ace"]


def test_player_map_aggregates_empty():
    assert player_map_aggregates([], "A") == {}


# --- team rating headline (item 2) ------------------------------------------

def test_team_rating_is_round_weighted_across_players():
    players = [
        {"rating": 1.0, "rounds": 100},
        {"rating": 2.0, "rounds": 300},
    ]
    assert team_rating(players) == (1.0 * 100 + 2.0 * 300) / 400


def test_team_rating_skips_missing_and_is_none_when_empty():
    players = [
        {"rating": None, "rounds": 100},   # no rating, skipped
        {"rating": 1.5, "rounds": 0},      # no rounds, skipped
        {"rating": 1.2, "rounds": 50},
    ]
    assert team_rating(players) == 1.2
    assert team_rating([]) is None


# --- pressure: decider, distance, comeback (item 9) -------------------------

def _series(match_id, order_winners, team_score, opp_score):
    """Per-map rows for one series: order_winners is [(map_order, winner), ...]."""
    return [
        {
            "match_id": match_id,
            "map_order": order,
            "winner_name": winner,
            "team_series_score": team_score,
            "opp_series_score": opp_score,
        }
        for order, winner in order_winners
    ]


def test_pressure_decider_counts_level_final_map_only():
    rows = []
    # A 2-1 series A wins: maps W, L, W. Going into map 3 it is 1-1, a decider.
    rows += _series(1, [(1, "A"), (2, "B"), (3, "A")], 2, 1)
    # A 2-0 sweep: going into map 2 it is 1-0, not level, so no decider.
    rows += _series(2, [(1, "A"), (2, "A")], 2, 0)
    out = pressure_stats(rows, "A")
    assert out["decider_played"] == 1
    assert out["decider_won"] == 1
    assert out["decider_winrate"] == 1.0
    # Won the series in the one decider, so distance win% is 1.0 over 1.
    assert out["distance_played"] == 1
    assert out["distance_series_won"] == 1
    assert out["distance_winrate"] == 1.0


def test_pressure_decider_lost_and_distance_tracks_series():
    # A 1-2 loss: maps W, L, L. Going into map 3 it is 1-1, a decider A lost.
    rows = _series(1, [(1, "A"), (2, "B"), (3, "B")], 1, 2)
    out = pressure_stats(rows, "A")
    assert out["decider_played"] == 1 and out["decider_won"] == 0
    assert out["decider_winrate"] == 0.0
    assert out["distance_series_won"] == 0 and out["distance_winrate"] == 0.0


def test_pressure_comeback_lost_opener_won_series():
    rows = []
    # Lost map 1, won the series 2-1: a comeback.
    rows += _series(1, [(1, "B"), (2, "A"), (3, "A")], 2, 1)
    # Lost map 1, lost the series: a comeback chance not converted.
    rows += _series(2, [(1, "B"), (2, "A"), (3, "B")], 1, 2)
    # Won map 1: not a comeback chance at all.
    rows += _series(3, [(1, "A"), (2, "A")], 2, 0)
    out = pressure_stats(rows, "A")
    assert out["comeback_chances"] == 2
    assert out["comeback_won"] == 1
    assert out["comeback_rate"] == 1 / 2


def test_pressure_bo1_is_not_a_decider():
    # A single decided map: level going in is 0-0 with no map won by each side,
    # so it must not count as a decider.
    rows = _series(1, [(1, "A")], 1, 0)
    out = pressure_stats(rows, "A")
    assert out["decider_played"] == 0


def test_pressure_skips_maps_with_no_order_and_handles_empty():
    rows = _series(1, [(None, "A"), (None, "B")], 2, 1)
    out = pressure_stats(rows, "A")
    assert out["decider_played"] == 0
    assert pressure_stats([], "A")["decider_winrate"] is None


# --- map-pool overlap lens (item 11) ----------------------------------------

def _split(winrate):
    return {"map_winrate": winrate}


def test_map_pool_overlap_labels_each_map():
    a = {"Ascent": _split(0.7), "Bind": _split(0.3), "Lotus": _split(0.6),
         "Haven": _split(0.4)}
    b = {"Ascent": _split(0.8), "Bind": _split(0.2), "Lotus": _split(0.4),
         "Haven": _split(None)}
    pool = ["Ascent", "Bind", "Lotus", "Haven"]
    out = {row["map"]: row["label"] for row in map_pool_overlap(a, b, pool)}
    assert out["Ascent"] == "shared strength"   # both >= 0.5
    assert out["Bind"] == "shared weakness"     # both < 0.5
    assert out["Lotus"] == "split"              # one strong, one weak
    assert out["Haven"] == "insufficient"       # b has no decided map


def test_map_pool_overlap_defaults_to_union_sorted():
    a = {"Bind": _split(0.6)}
    b = {"Ascent": _split(0.6)}
    out = map_pool_overlap(a, b)
    # No pool given: every map either team has, sorted by name.
    assert [row["map"] for row in out] == ["Ascent", "Bind"]
    # Each is insufficient since only one team has each map.
    assert all(row["label"] == "insufficient" for row in out)


# --- Wilson confidence interval (item 4) ------------------------------------

def test_wilson_interval_none_without_observations():
    assert wilson_interval(0, 0) is None


def test_wilson_interval_brackets_the_rate_and_stays_in_bounds():
    low, high = wilson_interval(6, 10)
    assert 0.0 <= low < 0.6 < high <= 1.0


def test_wilson_interval_tightens_with_more_data():
    thin = wilson_interval(60, 100)
    thick = wilson_interval(600, 1000)
    # Same 60% rate, but the larger sample gives a narrower band.
    assert (thick[1] - thick[0]) < (thin[1] - thin[0])


def test_wilson_interval_handles_the_extremes_without_running_past_bounds():
    low, high = wilson_interval(10, 10)
    assert high <= 1.0 and low < 1.0   # 100% still has a lower bound below 1
    low0, high0 = wilson_interval(0, 10)
    assert low0 >= 0.0 and high0 > 0.0


def test_bands_overlap_thin_samples_are_not_distinguishable():
    # 53% over ~20 vs 49% over ~20: wide bands that cross, so within noise.
    assert bands_overlap(11, 21, 10, 21) is True


def test_bands_overlap_separates_on_a_deep_lopsided_sample():
    # 70% over 1000 vs 40% over 1000: tight bands far apart, a real edge.
    assert bands_overlap(700, 1000, 400, 1000) is False


def test_bands_overlap_none_when_a_side_has_no_sample():
    assert bands_overlap(0, 0, 5, 10) is None
    assert bands_overlap(5, 10, 0, 0) is None


# --- per-player recent rating trajectory (P4) -------------------------------

def _recent_row(name, team, mdate, rating, rounds=20):
    return {"player_name": name, "team_name": team, "match_date": mdate,
            "rating": rating, "map_rounds": rounds}


def test_player_recent_ratings_uses_only_the_last_n_maps():
    from valtrack.stats import player_recent_ratings

    # Three old maps at 1.0, then two recent maps at 1.5: last 2 average to 1.5.
    rows = [
        _recent_row("ace", "A", "2026-01-01", 1.0),
        _recent_row("ace", "A", "2026-01-02", 1.0),
        _recent_row("ace", "A", "2026-01-03", 1.0),
        _recent_row("ace", "A", "2026-05-01", 1.5),
        _recent_row("ace", "A", "2026-05-02", 1.5),
    ]
    out = player_recent_ratings(rows, "A", last_maps=2)
    assert out["ace"]["recent_maps"] == 2
    assert abs(out["ace"]["recent_rating"] - 1.5) < 1e-9


def test_player_recent_ratings_round_weights_within_the_recent_window():
    from valtrack.stats import player_recent_ratings

    # Two recent maps: 2.0 over 10 rounds and 1.0 over 30 rounds -> 1.25 weighted.
    rows = [
        _recent_row("ace", "A", "2026-05-02", 2.0, rounds=10),
        _recent_row("ace", "A", "2026-05-01", 1.0, rounds=30),
    ]
    out = player_recent_ratings(rows, "A", last_maps=5)
    assert abs(out["ace"]["recent_rating"] - 1.25) < 1e-9


def test_player_recent_ratings_ignores_other_team_and_handles_no_rating():
    from valtrack.stats import player_recent_ratings

    rows = [
        _recent_row("ace", "A", "2026-05-01", None),
        _recent_row("opp", "B", "2026-05-01", 1.4),
    ]
    out = player_recent_ratings(rows, "A", last_maps=5)
    assert set(out) == {"ace"}
    assert out["ace"]["recent_rating"] is None  # the one map had no rating


# --- agent compositions per map (item 3) ------------------------------------

def _comp_row(match_id, map_name, team, agent, winner):
    return {"match_id": match_id, "map_name": map_name, "team_name": team,
            "agent": agent, "winner_name": winner}


def test_map_compositions_tallies_and_orders_by_play_count():
    team = "PRX"
    rows = []
    # Two maps of the same comp on Lotus, one won one lost.
    for mid, winner in ((1, "PRX"), (2, "EDG")):
        for ag in ("Jett", "Omen", "Sova", "Killjoy", "Skye"):
            rows.append(_comp_row(mid, "Lotus", team, ag, winner))
    # One map of a different comp on Lotus, won.
    for ag in ("Raze", "Omen", "Sova", "Killjoy", "Skye"):
        rows.append(_comp_row(3, "Lotus", team, ag, "PRX"))
    # An opponent row that must be ignored.
    rows.append(_comp_row(1, "Lotus", "EDG", "Yoru", "PRX"))

    out = map_compositions(rows, team)
    lotus = out["Lotus"]
    assert lotus[0]["played"] == 2          # the Jett comp leads on play count
    assert lotus[0]["won"] == 1
    assert lotus[0]["winrate"] == 0.5
    assert "Jett" in lotus[0]["agents"] and "Yoru" not in lotus[0]["agents"]
    assert lotus[1]["played"] == 1 and lotus[1]["winrate"] == 1.0


# --- map duel board cross-side framing (item 22) ----------------------------

def test_map_duel_board_pairs_attack_against_defense():
    a = {"Lotus": {"map_winrate": 0.6, "atk_winrate": 0.7, "def_winrate": 0.5,
                   "rounds_total": 40}}
    b = {"Lotus": {"map_winrate": 0.55, "atk_winrate": 0.61, "def_winrate": 0.55,
                   "rounds_total": 30}}
    row = map_duel_board(a, b, ["Lotus"])[0]
    # The cross-side duel: A attacking lines up against B defending, and mirror.
    assert (row["a_atk"], row["b_def"]) == (0.7, 0.55)
    assert (row["b_atk"], row["a_def"]) == (0.61, 0.5)
    assert row["a_rounds"] == 40 and row["b_rounds"] == 30


def test_map_duel_board_missing_side_is_none():
    a = {"Lotus": {"map_winrate": 0.6, "atk_winrate": 0.7, "def_winrate": 0.5,
                   "rounds_total": 40}}
    row = map_duel_board(a, {}, ["Lotus"])[0]
    assert row["b_atk"] is None and row["b_map"] is None and row["b_rounds"] == 0


# --- gap ranking (item 24) --------------------------------------------------

def test_rank_metric_gaps_orders_by_absolute_gap_and_tags_leader():
    metrics = [
        {"metric": "Win %", "a": 60.0, "b": 55.0},     # gap 5, a leads
        {"metric": "Pistol %", "a": 40.0, "b": 70.0},  # gap -30, b leads
        {"metric": "Rating", "a": 1.1, "b": 1.1},      # tie
        {"metric": "Opening %", "a": None, "b": 50.0}, # missing, sorts last
    ]
    out = rank_metric_gaps(metrics)
    assert [r["metric"] for r in out] == [
        "Pistol %", "Win %", "Rating", "Opening %"]
    assert out[0]["leader"] == "b" and out[0]["gap"] == -30.0
    assert out[1]["leader"] == "a"
    assert out[2]["leader"] is None          # a tie has no leader
    assert out[3]["gap"] is None             # a missing side has no gap


# --- economy conversion (item 1) --------------------------------------------

def _econ(team, buy, played, won):
    return {"team_name": team, "buy_type": buy, "played": played, "won": won}


def test_economy_conversion_win_rate_per_buy_type():
    # Aggregate buy-type rows (per map), summed across maps in the window.
    rows = [
        _econ("PRX", "eco", 2, 1),
        _econ("PRX", "eco", 3, 2),     # eco totals: 5 played, 3 won
        _econ("PRX", "full", 10, 8),
        _econ("EDG", "eco", 4, 4),     # ignored
    ]
    out = economy_conversion(rows, "PRX")
    assert out["eco"] == {"won": 3, "total": 5, "winrate": 0.6}
    assert out["full"]["winrate"] == 0.8
    assert "EDG" not in str(out)


def test_economy_conversion_handles_null_counts():
    rows = [_econ("PRX", "eco", None, None), _econ("PRX", "eco", 2, 1)]
    out = economy_conversion(rows, "PRX")
    assert out["eco"] == {"won": 1, "total": 2, "winrate": 0.5}


# --- clutch stats (item 2): won-only counts, no win rate ---------------------

def _perf(name, team, **kw):
    base = {"player_name": name, "team_name": team,
            "mk_2k": 0, "mk_3k": 0, "mk_4k": 0, "mk_5k": 0,
            "clutch_1v1": 0, "clutch_1v2": 0, "clutch_1v3": 0,
            "clutch_1v4": 0, "clutch_1v5": 0, "plants": 0, "defuses": 0}
    base.update(kw)
    return base


def test_clutch_stats_counts_wins_by_depth_no_rate():
    rows = [
        _perf("f0rsakeN", "PRX", clutch_1v1=2, clutch_1v2=1),
        _perf("f0rsakeN", "PRX", clutch_1v3=1),
        _perf("something", "PRX", clutch_1v1=1),
        _perf("x", "EDG", clutch_1v1=5),  # ignored
    ]
    out = clutch_stats(rows, "PRX")
    assert out["won"] == 5                       # 2+1+1 + 1
    assert out["by_depth"] == {1: 3, 2: 1, 3: 1, 4: 0, 5: 0}
    # Sorted by clutches won; no win rate is reported (attempts are unavailable).
    assert out["players"][0]["player_name"] == "f0rsakeN"
    assert out["players"][0]["won"] == 4
    assert out["players"][0]["deepest"] == 3
    assert "winrate" not in out and "winrate" not in out["players"][0]
    assert "EDG" not in str(out)


# --- multikills and utility (item: performance) -----------------------------

def test_multikill_stats_orders_rarer_kills_first():
    rows = [
        _perf("a", "PRX", mk_2k=10, mk_3k=1),
        _perf("a", "PRX", mk_2k=3, mk_4k=1),     # a: 13 2K, 1 3K, 1 4K
        _perf("b", "PRX", mk_5k=1, mk_2k=1),     # b has the only ace
        _perf("z", "EDG", mk_5k=9),              # ignored
    ]
    out = multikill_stats(rows, "PRX")
    assert out[0]["player_name"] == "b"          # the 5K sorts first
    a = next(p for p in out if p["player_name"] == "a")
    assert (a["k2"], a["k3"], a["k4"], a["k5"]) == (13, 1, 1, 0)
    assert a["total"] == 15
    assert "EDG" not in str(out)


def test_utility_stats_totals_and_per_player():
    rows = [
        _perf("controller", "PRX", plants=12, defuses=2),
        _perf("controller", "PRX", plants=7, defuses=1),
        _perf("duelist", "PRX", plants=0, defuses=3),
        _perf("z", "EDG", plants=99, defuses=99),  # ignored
    ]
    out = utility_stats(rows, "PRX")
    assert out["plants"] == 19 and out["defuses"] == 6
    assert out["players"][0]["player_name"] == "controller"
    assert out["players"][0]["plants"] == 19
    assert "EDG" not in str(out)


# --- round win conditions (item: round win-condition) -----------------------

def test_round_win_conditions_split_by_side():
    rows = [
        {"winner_side": "def", "win_type": "defuse", "n": 4},
        {"winner_side": "def", "win_type": "time", "n": 3},
        {"winner_side": "atk", "win_type": "boom", "n": 5},
        {"winner_side": "atk", "win_type": "elim", "n": 6},
        {"winner_side": "def", "win_type": "elim", "n": 2},
    ]
    out = round_win_conditions(rows)
    assert out["total"] == 20
    assert out["by_type"]["elim"] == 8        # 6 atk + 2 def
    assert out["by_side"]["def"]["defuse"] == 4
    assert out["by_side"]["atk"]["boom"] == 5
    assert out["by_side"]["atk"]["defuse"] == 0


def test_round_win_conditions_empty():
    out = round_win_conditions([])
    assert out["total"] == 0
    assert out["by_type"] == {"elim": 0, "defuse": 0, "time": 0, "boom": 0}


# --- player-name dedup (item 7) ---------------------------------------------

def test_infer_match_format_from_series_score():
    assert infer_match_format(3, 1) == "Bo5"   # first to 3
    assert infer_match_format(0, 3) == "Bo5"
    assert infer_match_format(2, 0) == "Bo3"   # first to 2
    assert infer_match_format(1, 0) == "Bo1"
    assert infer_match_format(None, 2) is None
    assert infer_match_format(0, 0) is None


def test_canonical_player_name_strips_trailing_digits_when_safe():
    assert canonical_player_name("Moonlight") == "moonlight"
    assert canonical_player_name("MOONLIGHT1") == "moonlight"
    # Too short after stripping, so a numeric-ish short handle is left alone.
    assert canonical_player_name("s0m") == "s0m"


def test_merge_player_aliases_unifies_variants_to_one_display():
    rows = [
        {"player_name": "Moonlight", "x": 1},
        {"player_name": "Moonlight", "x": 2},
        {"player_name": "MOONLIGHT1", "x": 3},
        {"player_name": None, "x": 4},
    ]
    merged = merge_player_aliases(rows)
    names = [r["player_name"] for r in merged]
    # All three variants collapse to the most frequent spelling, none lost.
    assert names[:3] == ["Moonlight", "Moonlight", "Moonlight"]
    assert names[3] is None
    # And aggregating now sees one player, not two.
    assert len({canonical_player_name(n) for n in names if n}) == 1


# --- round-after-pistol conversion (v2 batch 1, item 1) ---------------------

def _pro_round(match_id, map_name, rn, winner, pistol=False):
    return {"match_id": match_id, "map_name": map_name, "round_number": rn,
            "winner_team": winner, "is_pistol": pistol}


def test_post_pistol_conversion_hand_checked():
    # A wins the round-1 pistol then wins round 2 (a conversion); A loses the
    # round-13 pistol then wins round 14 anyway (a recovery / break).
    rows = [
        _pro_round("m1", "Ascent", 1, "A", pistol=True),
        _pro_round("m1", "Ascent", 2, "A"),
        _pro_round("m1", "Ascent", 13, "B", pistol=True),
        _pro_round("m1", "Ascent", 14, "A"),
    ]
    out = post_pistol_conversion(rows, "A")
    assert out["won_pistols"] == 1 and out["won_then_won"] == 1
    assert out["won_conv_rate"] == 1.0
    assert out["lost_pistols"] == 1 and out["lost_then_won"] == 1
    assert out["lost_recover_rate"] == 1.0


def test_post_pistol_conversion_groups_per_map_and_skips_missing_next():
    rows = [
        # Map 1: won pistol, lost round 2.
        _pro_round("m1", "Ascent", 1, "A", pistol=True),
        _pro_round("m1", "Ascent", 2, "B"),
        # Map 2 (same map name, different match): lost pistol, lost round 2.
        _pro_round("m2", "Ascent", 1, "B", pistol=True),
        _pro_round("m2", "Ascent", 2, "B"),
        # A pistol with no stored next round contributes nothing.
        _pro_round("m2", "Ascent", 13, "A", pistol=True),
    ]
    out = post_pistol_conversion(rows, "A")
    assert out["won_pistols"] == 1 and out["won_then_won"] == 0
    assert out["lost_pistols"] == 1 and out["lost_then_won"] == 0
    assert out["won_conv_rate"] == 0.0 and out["lost_recover_rate"] == 0.0


def test_post_pistol_conversion_empty():
    out = post_pistol_conversion([], "A")
    assert out["won_pistols"] == 0 and out["won_conv_rate"] is None


# --- close-game and round-margin profile (v2 batch 1, item 4) ---------------

def _scored_map(map_name, t1, t2, us, them):
    winner = t1 if us > them else t2
    return {"map_name": map_name, "team1_name": t1, "team2_name": t2,
            "team1_score": us, "team2_score": them, "winner_name": winner}


def test_margin_profile_hand_checked():
    rows = [
        _scored_map("Ascent", "A", "B", 13, 5),    # comfortable win, margin 8
        _scored_map("Bind", "A", "B", 13, 11),     # close win, margin 2
        _scored_map("Lotus", "A", "B", 14, 12),    # overtime win, close
        _scored_map("Split", "B", "A", 13, 9),     # loss by 4 (A is team2)
    ]
    out = margin_profile(rows, "A")
    assert out["maps"] == 4
    # Close: Bind (2) and Lotus (OT, margin 2) are within two rounds, both won.
    assert out["close_played"] == 2 and out["close_won"] == 2
    assert out["close_winrate"] == 1.0
    # Overtime: only Lotus (the loser finished on 12+).
    assert out["ot_played"] == 1 and out["ot_won"] == 1
    # Winning margins 8, 2, 2 -> mean 4; the one loss margin is 4.
    assert out["avg_win_margin"] == 4.0
    assert out["avg_loss_margin"] == 4.0


def test_margin_profile_skips_undecided_and_foreign_rows():
    rows = [
        _scored_map("Ascent", "A", "B", 13, 7),
        {"map_name": "Bind", "team1_name": "A", "team2_name": "B",
         "team1_score": None, "team2_score": None, "winner_name": None},
        _scored_map("Lotus", "C", "D", 13, 4),   # A not in this map
    ]
    out = margin_profile(rows, "A")
    assert out["maps"] == 1


# --- prediction calibration on the matchup log (v2 batch 1, item 6) ---------

def _log(conf, pred, outcome):
    return {"confidence": conf, "predicted_side": pred, "outcome_side": outcome}


def test_calibration_buckets_by_confidence_and_scores_hits():
    entries = [
        _log("high", "a", "a"),     # correct
        _log("high", "a", "b"),     # wrong
        _log("high", "b", "b"),     # correct
        _log("low", "a", "b"),      # wrong
        _log("medium", "a", None),  # unresolved, ignored
        _log("medium", None, "a"),  # no prediction, ignored
    ]
    out = calibration(entries)
    assert out["resolved"] == 4 and out["correct"] == 2
    assert out["rate"] == 0.5
    buckets = {b["confidence"]: b for b in out["buckets"]}
    assert buckets["high"]["resolved"] == 3 and buckets["high"]["correct"] == 2
    assert buckets["low"]["resolved"] == 1 and buckets["low"]["correct"] == 0
    # Buckets are ordered low to high confidence.
    assert [b["confidence"] for b in out["buckets"]] == ["low", "high"]


def test_calibration_empty():
    out = calibration([])
    assert out == {"buckets": [], "resolved": 0, "correct": 0, "rate": None}


# --- lineup continuity for the window (v2 batch 1, item 3) ------------------

def _pmap(match_id, map_name, player):
    return {"match_id": match_id, "map_name": map_name, "team_name": "A",
            "player_name": player}


def test_lineup_continuity_counts_maps_played_by_current_five_only():
    five = {"a1", "a2", "a3", "a4", "a5"}
    rows = []
    # Map 1: all five current starters.
    for p in ["a1", "a2", "a3", "a4", "a5"]:
        rows.append(_pmap("m1", "Ascent", p))
    # Map 2: a stand-in replaced a5, so this map is not "by the current five".
    for p in ["a1", "a2", "a3", "a4", "sub"]:
        rows.append(_pmap("m2", "Bind", p))
    out = lineup_continuity(rows, five)
    assert out["maps_total"] == 2 and out["maps_current"] == 1
    assert out["pct"] == 0.5


def test_lineup_continuity_none_without_a_current_five():
    assert lineup_continuity([_pmap("m1", "Ascent", "a1")], set()) is None


# --- player consistency / dispersion (v2 batch 2, item 3) -------------------

def _spread_row(player, rating, map_name):
    return {"team_name": "A", "player_name": player, "player_id": 1,
            "map_name": map_name, "agent": "Jett", "rating": rating, "acs": 200,
            "kills": 15, "deaths": 12, "assists": 4, "kast": "70%", "adr": 140,
            "hs_pct": "20%", "first_kills": 2, "first_deaths": 2,
            "map_rounds": 24}


def test_player_aggregates_reports_rating_spread():
    # Two maps at 1.00 and 1.40 -> mean 1.20, range 1.00-1.40.
    rows = [_spread_row("ace", 1.0, "Ascent"), _spread_row("ace", 1.4, "Bind")]
    p = player_aggregates(rows, "A")[0]
    sp = p["rating_spread"]
    assert sp["min"] == 1.0 and sp["max"] == 1.4 and sp["n"] == 2
    assert sp["std"] > 0
    # One map gives no dispersion.
    one = player_aggregates([rows[0]], "A")[0]["rating_spread"]
    assert one["std"] is None and one["min"] == 1.0


# --- league reference points: percentile and field summary (batch 3, item 1)

def test_percentile_midpoint_convention():
    pop = [10, 20, 30, 40]
    assert percentile(30, pop) == 100.0 * (2 + 0.5) / 4   # 2 below, 1 equal
    assert percentile(5, pop) == 0.0
    assert percentile(50, pop) == 100.0
    assert percentile(None, pop) is None
    assert percentile(20, []) is None


def test_field_summary_basic_and_empty():
    s = field_summary([10, None, 30, 20])
    assert s["n"] == 3 and s["min"] == 10 and s["max"] == 30
    assert s["median"] == 20 and s["mean"] == 20
    empty = field_summary([None, None])
    assert empty == {"n": 0, "min": None, "max": None, "median": None,
                     "mean": None}


# --- opponent-tier split (v2 batch 1, item 5) -------------------------------

def test_tier_of_rank_buckets():
    assert tier_of_rank(1) == "top10"
    assert tier_of_rank(10) == "top10"
    assert tier_of_rank(11) == "mid"
    assert tier_of_rank(30) == "mid"
    assert tier_of_rank(31) == "rest"
    assert tier_of_rank(None) == "rest"   # unranked falls into rest


def test_partition_by_tier_splits_rows():
    rows = [
        {"opp_rank": 3, "x": 1}, {"opp_rank": 20, "x": 2},
        {"opp_rank": 50, "x": 3}, {"opp_rank": None, "x": 4},
    ]
    buckets = partition_by_tier(rows)
    assert {r["x"] for r in buckets["top10"]} == {1}
    assert {r["x"] for r in buckets["mid"]} == {2}
    assert {r["x"] for r in buckets["rest"]} == {3, 4}   # unranked joins rest
