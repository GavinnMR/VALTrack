"""Tests for veto-tendency aggregation and map-pool reconstruction.

This is the flagship feature and the easiest to corrupt quietly, so the
tendencies and the reconstruction are pinned to hand-built vetos with asserted
outputs: tag resolution (including a mojibake token), junk rows dropped, and the
pick, decider, and ban assignment following from known pick and ban rates.
"""
from valtrack.veto import (
    active_pool,
    reconstruct,
    team_tendencies,
)


def _row(match_id, token, action, map_name):
    return {
        "match_id": match_id,
        "team_token": token,
        "action": action,
        "map_name": map_name,
    }


def test_team_tendencies_counts_and_rates():
    rows = [
        # Match 1: pool is Ascent, Bind, Haven. PRX bans Ascent, picks Bind.
        _row(1, "PRX", "ban", "Ascent"),
        _row(1, "OPP", "ban", "Haven"),
        _row(1, "PRX", "pick", "Bind"),
        _row(1, None, "remains", "Haven"),
        # Match 2: PRX bans Ascent again, picks Haven this time.
        _row(2, "PRX", "ban", "Ascent"),
        _row(2, "PRX", "pick", "Haven"),
        _row(2, "OPP", "pick", "Bind"),
    ]
    out = team_tendencies(rows, "PRX")
    # Ascent: in both pools, PRX banned both times -> ban_rate 1.0, never picked.
    assert out["Ascent"]["appearances"] == 2
    assert out["Ascent"]["bans"] == 2 and out["Ascent"]["picks"] == 0
    assert out["Ascent"]["ban_rate"] == 1.0
    assert out["Ascent"]["pick_rate"] == 0.0
    # Bind: appeared twice, PRX picked once -> pick_rate 1/2.
    assert out["Bind"]["appearances"] == 2
    assert out["Bind"]["picks"] == 1 and out["Bind"]["pick_rate"] == 0.5
    # Haven: appeared twice, PRX picked once.
    assert out["Haven"]["appearances"] == 2
    assert out["Haven"]["picks"] == 1


def test_team_tendencies_resolves_accented_tag():
    # An accented tag must resolve. In the database both teams.tag and the veto
    # token are stored as the same correct UTF-8 ("KRÜ"); the console may render
    # it oddly, but the comparison is byte for byte and case insensitive.
    rows = [_row(1, "krü", "pick", "Lotus")]
    out = team_tendencies(rows, "KRÜ")
    assert out["Lotus"]["picks"] == 1


def test_team_tendencies_drops_non_map_junk():
    rows = [
        _row(1, "PRX", "pick", "Bind"),
        _row(1, None, None, "Stats unavailable for Map 1 due to lobby remake."),
        _row(1, None, None, "This match was played on console"),
        _row(1, "PRX", "ban", "The TANDIS Hammers pick Split"),  # broken segment
    ]
    out = team_tendencies(rows, "PRX")
    assert set(out) == {"Bind"}


def test_team_tendencies_tracks_last_seen():
    rows = [
        {"match_id": 1, "team_token": "PRX", "action": "pick",
         "map_name": "Bind", "match_date": "2026-01-01"},
        {"match_id": 2, "team_token": "PRX", "action": "ban",
         "map_name": "Bind", "match_date": "2026-03-01"},
    ]
    out = team_tendencies(rows, "PRX")
    assert out["Bind"]["last_seen"] == "2026-03-01"


def test_active_pool_prefers_recent_maps_over_stale_volume():
    # An old map seen often vs current maps seen less: with recency, the stale map
    # drops out of the pool despite its volume.
    a = {
        "OldMap": {"appearances": 50, "last_seen": "2021-01-01"},
        "NewMap": {"appearances": 5, "last_seen": "2026-06-01"},
        "Ascent": {"appearances": 20, "last_seen": "2026-05-01"},
    }
    pool = active_pool(a, {}, size=2)
    assert "OldMap" not in pool
    assert set(pool) == {"Ascent", "NewMap"}


def test_active_pool_without_dates_falls_back_to_appearances():
    # No last_seen anywhere: behaves like the old appearance-only ranking.
    a = {m: {"appearances": n} for m, n in
         {"Ascent": 5, "Bind": 9, "Haven": 2}.items()}
    assert active_pool(a, {}, size=2) == ["Bind", "Ascent"]


def test_active_pool_takes_most_seen_maps():
    a = {m: {"appearances": n} for m, n in
         {"Ascent": 5, "Bind": 4, "Haven": 3, "Lotus": 2}.items()}
    b = {m: {"appearances": n} for m, n in
         {"Ascent": 1, "Split": 6, "Pearl": 5}.items()}
    pool = active_pool(a, b, size=4)
    # Combined appearances: Ascent 6, Split 6, Pearl 5, Bind 4, Haven 3, Lotus 2.
    assert pool == ["Ascent", "Split", "Pearl", "Bind"]


def _tend(**maps):
    """Build a tendencies dict from {map: (pick_rate, ban_rate)} pairs."""
    out = {}
    for name, (pr, br) in maps.items():
        out[name] = {
            "appearances": 10, "bans": 0, "picks": 0,
            "pick_rate": pr, "ban_rate": br,
        }
    return out


def test_reconstruct_assigns_picks_decider_and_bans():
    pool = ["Ascent", "Bind", "Haven", "Lotus", "Split", "Pearl", "Sunset"]
    # A clearly favors Ascent; B clearly favors Bind. Haven is liked by both and
    # banned by neither, so it should survive as the decider. The rest are banned.
    a = _tend(
        Ascent=(0.8, 0.0), Bind=(0.1, 0.2), Haven=(0.3, 0.0), Lotus=(0.0, 0.7),
        Split=(0.0, 0.6), Pearl=(0.0, 0.8), Sunset=(0.1, 0.5),
    )
    b = _tend(
        Ascent=(0.1, 0.2), Bind=(0.8, 0.0), Haven=(0.3, 0.0), Lotus=(0.0, 0.6),
        Split=(0.0, 0.7), Pearl=(0.0, 0.5), Sunset=(0.0, 0.8),
    )
    rec = reconstruct(a, b, pool)
    assert rec["a_pick"] == "Ascent"
    assert rec["b_pick"] == "Bind"
    assert rec["decider"] == "Haven"
    assert set(rec["likely_played"]) == {"Ascent", "Bind", "Haven"}
    assert set(rec["likely_bans"]) == {"Lotus", "Split", "Pearl", "Sunset"}
    # Bans are ordered most-likely-banned (lowest play_score) first.
    assert rec["likely_bans"][0] in {"Pearl", "Sunset"}


def test_reconstruct_breaks_shared_favorite():
    pool = ["Ascent", "Bind", "Haven"]
    # Both teams favor Ascent most. A takes it; B must fall to its next, Bind.
    a = _tend(Ascent=(0.9, 0.0), Bind=(0.2, 0.0), Haven=(0.1, 0.0))
    b = _tend(Ascent=(0.9, 0.0), Bind=(0.5, 0.0), Haven=(0.1, 0.0))
    rec = reconstruct(a, b, pool)
    assert rec["a_pick"] == "Ascent"
    assert rec["b_pick"] == "Bind"
    assert rec["decider"] == "Haven"


def test_reconstruct_empty_pool():
    rec = reconstruct({}, {}, [])
    assert rec["a_pick"] is None
    assert rec["b_pick"] is None
    assert rec["decider"] is None
    assert rec["likely_played"] == []
    assert rec["rows"] == []
