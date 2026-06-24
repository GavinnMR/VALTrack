"""Tests for resolving the upcoming-match feed to franchise pairs (P5).

The matching is the part that can quietly go wrong (a feed name that does not
line up with a stored team, or a loose match colliding two teams into one), so it
is pinned here against hand-built segments. The API fetch itself is not tested;
the app fetches and this resolves.
"""
from valtrack.schedule import franchise_upcoming


def _teams():
    return [
        {"id": 1, "name": "Paper Rex", "tag": "PRX"},
        {"id": 2, "name": "Cloud9", "tag": "C9"},
        {"id": 3, "name": "Sentinels", "tag": "SEN"},
    ]


def _seg(team1, team2, event="VCT", ts="1893456000"):
    return {"team1": team1, "team2": team2, "match_event": event,
            "unix_timestamp": ts}


def test_keeps_only_franchise_versus_franchise():
    segs = [
        _seg("Paper Rex", "Cloud9"),         # both franchise -> kept
        _seg("Paper Rex", "Some Tier 2 Org"),  # opponent not franchise -> dropped
        _seg("Random A", "Random B"),        # neither franchise -> dropped
    ]
    pairs = franchise_upcoming(segs, _teams())
    assert len(pairs) == 1
    assert pairs[0]["a"]["name"] == "Paper Rex"
    assert pairs[0]["b"]["name"] == "Cloud9"


def test_resolves_by_tag_and_reads_event_and_date():
    # The feed uses the tag for one side; it still resolves.
    segs = [_seg("PRX", "Sentinels", event="Masters Toronto")]
    pairs = franchise_upcoming(segs, _teams())
    assert len(pairs) == 1
    p = pairs[0]
    assert p["a"]["id"] == 1 and p["b"]["id"] == 3
    assert p["is_lan"] is True            # Masters is an inferred LAN event
    assert p["match_date"] == "2030-01-01"  # the fixed timestamp, as a UTC date


def test_drops_segment_when_both_sides_resolve_to_same_team():
    # A loose name that could match two ways must not pair a team with itself.
    segs = [_seg("Paper Rex", "Paper Rex Academy")]
    pairs = franchise_upcoming(segs, _teams())
    assert pairs == []


def test_missing_timestamp_yields_no_date():
    segs = [{"team1": "Paper Rex", "team2": "Cloud9", "match_event": "VCT",
             "unix_timestamp": ""}]
    pairs = franchise_upcoming(segs, _teams())
    assert pairs[0]["match_date"] is None
