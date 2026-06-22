"""Tests for the pure derivations: form and streak, and roster classification.

These are the bits that can silently mislead, so they are pinned to known
inputs: a streak must count the full current run, and the roster split must
survive VLR's unreliable staff flag and its occasionally mangled role text.
"""
from valtrack.stats import classify_roster, form_and_streak


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
