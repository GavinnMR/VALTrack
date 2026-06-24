"""A hermetic smoke test that the Streamlit app boots and renders.

The UI is verified by hand, but this guards the wiring: it seeds a small but
complete database (two teams with a detailed match), points the app at it, and
asserts the script runs end to end with no exception, including the current-five
and event-type toggles. It is not a substitute for visual checks; it catches the
"a query changed shape and a render crashes" class of regression.
"""
from streamlit.testing.v1 import AppTest

from valtrack import db


def _seed(path):
    db.init_db(path)
    conn = db.connect(path)

    conn.execute(
        "INSERT INTO teams (id, name, tag, league, region, regional_rank) "
        "VALUES (1, 'Alpha', 'ALP', 'americas', 'na', 1)"
    )
    conn.execute(
        "INSERT INTO teams (id, name, tag, league, region, regional_rank) "
        "VALUES (2, 'Beta', 'BET', 'emea', 'eu', 2)"
    )
    for pid, alias, tid in [(10, "a1", 1), (11, "a2", 1), (20, "b1", 2), (21, "b2", 2)]:
        conn.execute("INSERT INTO players (id, alias) VALUES (?, ?)", (pid, alias))
        conn.execute(
            "INSERT INTO rosters (team_id, player_id, role) VALUES (?, ?, '')",
            (tid, pid),
        )

    conn.execute(
        """
        INSERT INTO matches (
            match_id, team1_id, team1_name, team1_tag,
            team2_id, team2_name, team2_tag,
            team1_score, team2_score, date, event_name, event_round,
            details_fetched_at
        ) VALUES (
            1, 1, 'Alpha', 'ALP', 2, 'Beta', 'BET',
            2, 1, '2026-01-10', 'Champions Tour 2026: Masters', 'MF', '2026-01-11'
        )
        """
    )
    conn.execute(
        "INSERT INTO map_results (match_id, map_name, team1_name, team2_name, "
        "team1_score, team2_score, winner_name, picked_by_name) "
        "VALUES (1, 'Ascent', 'Alpha', 'Beta', 13, 7, 'Alpha', 'Beta')"
    )
    rounds = [
        (1, "atk", "Alpha", 1, "elim"), (13, "def", "Beta", 1, "defuse"),
        (2, "atk", "Alpha", 0, "boom"), (14, "def", "Alpha", 0, "time"),
    ]
    for number, side, team, pistol, win_type in rounds:
        conn.execute(
            "INSERT INTO rounds (match_id, map_name, round_number, winner_side, "
            "winner_team, is_pistol, win_type) VALUES (1, 'Ascent', ?, ?, ?, ?, ?)",
            (number, side, team, pistol, win_type),
        )
    # Series-level performance and per-map economy, so the clutch, multikill,
    # utility, win-condition, and economy views render with data, not just their
    # empty states.
    for name, team in (("a1", "Alpha"), ("a2", "Alpha"), ("b1", "Beta"), ("b2", "Beta")):
        conn.execute(
            "INSERT INTO match_player_perf (match_id, player_name, team_name, "
            "mk_2k, mk_3k, mk_4k, mk_5k, clutch_1v1, clutch_1v2, clutch_1v3, "
            "clutch_1v4, clutch_1v5, plants, defuses) "
            "VALUES (1, ?, ?, 4, 1, 0, 0, 1, 0, 0, 0, 0, 2, 1)",
            (name, team),
        )
    for team in ("Alpha", "Beta"):
        for buy, played, won in (("eco", 3, 1), ("light", 2, 0),
                                 ("half", 4, 2), ("full", 8, 6)):
            conn.execute(
                "INSERT INTO map_economy (match_id, map_name, team_name, buy_type, "
                "played, won) VALUES (1, 'Ascent', ?, ?, ?, ?)",
                (team, buy, played, won),
            )
    players = [
        ("a1", "Alpha", "Jett"), ("a2", "Alpha", "Sova"),
        ("b1", "Beta", "Raze"), ("b2", "Beta", "Omen"),
    ]
    for name, team, agent in players:
        conn.execute(
            """
            INSERT INTO map_player_stats (
                match_id, map_name, player_name, team_name, agent,
                rating, acs, kills, deaths, assists, kast, adr, hs_pct,
                first_kills, first_deaths,
                first_kills_atk, first_kills_def, first_deaths_atk, first_deaths_def
            ) VALUES (1, 'Ascent', ?, ?, ?, 1.1, 230, 18, 12, 5, '74%', 155, '24%',
                      4, 3, 2, 2, 1, 2)
            """,
            (name, team, agent),
        )
    conn.execute(
        "INSERT INTO match_vetos (match_id, seq, team_token, action, map_name) "
        "VALUES (1, 1, 'ALP', 'ban', 'Bind')"
    )
    conn.execute(
        "INSERT INTO match_vetos (match_id, seq, team_token, action, map_name) "
        "VALUES (1, 2, 'BET', 'pick', 'Ascent')"
    )
    db.set_meta(conn, "last_updated", "2026-01-11T00:00:00+00:00")
    db.set_meta(conn, "last_status", "ok")
    conn.commit()
    conn.close()


def _by_key(elements, key):
    """Find a widget by its key, so the test does not depend on widget order."""
    return next(e for e in elements if e.key == key)


def _by_label(elements, label):
    return next(e for e in elements if e.label == label)


def test_app_boots_and_toggles_without_exception(tmp_path, monkeypatch):
    path = tmp_path / "app.db"
    _seed(path)
    # Point the app's database access at the seeded temp database.
    monkeypatch.setattr(db, "DB_PATH", path)
    real_connect = db.connect
    monkeypatch.setattr(db, "connect", lambda *a, **k: real_connect(path))

    at = AppTest.from_file("app.py").run(timeout=120)
    assert not at.exception
    # The at-a-glance strip and other tables render.
    assert len(at.dataframe) > 0

    # Current-five filter on.
    _by_key(at.checkbox, "five").set_value(True).run(timeout=120)
    assert not at.exception

    # LAN event filter on (the seeded match is a Masters event).
    _by_key(at.radio, "env").set_value("International LAN").run(timeout=120)
    assert not at.exception

    # A date preset that excludes the seeded match exercises the empty states.
    _by_key(at.radio, "env").set_value("All").run(timeout=120)
    _by_key(at.radio, "dwmode").set_value("Last 3 months").run(timeout=120)
    assert not at.exception

    # Back to all time, then the aligned view with its delta tables.
    _by_key(at.radio, "dwmode").set_value("All time").run(timeout=120)
    _by_key(at.radio, "view").set_value("Aligned").run(timeout=120)
    assert not at.exception

    # Swap the two teams.
    _by_key(at.radio, "view").set_value("Side by side").run(timeout=120)
    _by_label(at.button, "Swap A and B").click().run(timeout=120)
    assert not at.exception


def test_app_save_and_remove_favorite(tmp_path, monkeypatch):
    path = tmp_path / "app.db"
    _seed(path)
    monkeypatch.setattr(db, "DB_PATH", path)
    real_connect = db.connect
    monkeypatch.setattr(db, "connect", lambda *a, **k: real_connect(path))

    at = AppTest.from_file("app.py").run(timeout=120)
    # The save star exists for an unsaved pair; clicking it saves the matchup.
    save = [b for b in at.button if b.label == "☆ Save"]
    assert save
    save[0].click().run(timeout=120)
    assert not at.exception
    # Now the pair reads as saved.
    assert [b for b in at.button if b.label == "★ Saved"]


def test_app_palette_and_reset_controls(tmp_path, monkeypatch):
    path = tmp_path / "app.db"
    _seed(path)
    monkeypatch.setattr(db, "DB_PATH", path)
    real_connect = db.connect
    monkeypatch.setattr(db, "connect", lambda *a, **k: real_connect(path))

    at = AppTest.from_file("app.py").run(timeout=120)
    _by_key(at.selectbox, "palette").set_value(
        "Colorblind-safe (blue/orange)").run(timeout=120)
    assert not at.exception
    # Reset returns to the default view without error.
    _by_label(at.button, "Reset view").click().run(timeout=120)
    assert not at.exception


def test_app_matchup_log_add_and_resolve(tmp_path, monkeypatch):
    path = tmp_path / "app.db"
    _seed(path)
    monkeypatch.setattr(db, "DB_PATH", path)
    real_connect = db.connect
    monkeypatch.setattr(db, "connect", lambda *a, **k: real_connect(path))

    at = AppTest.from_file("app.py").run(timeout=120)
    # Add a log entry through the form, then resolve it with a structured winner.
    _by_key(at.text_area, "log_note_input").set_value("lean Alpha")
    _by_label(at.button, "Add to log").click().run(timeout=120)
    assert not at.exception
    # The structured winner radio and the save button now exist for the entry.
    save = [b for b in at.button if b.label == "Save outcome"]
    assert save
    save[0].click().run(timeout=120)
    assert not at.exception
