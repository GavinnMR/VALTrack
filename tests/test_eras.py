"""Tests for the rough patch-era labels behind the meta banner.

The labels are deliberately coarse, but the span logic must collapse a same-era
range to one label, widen a cross-era range, and never invent an era from a
missing date.
"""
from valtrack.eras import era_label, patch_era_span


def test_era_label_by_year():
    assert era_label("2020-05-05") == "2020 (closed beta and launch)"
    assert era_label("2023-07-01") == "2023 (Episodes 6-7)"
    assert era_label("2025-02-10") == "2025 (VCT)"
    # A year past the table still gets a sensible VCT-year label.
    assert era_label("2027-01-01") == "2027 (VCT)"
    assert era_label(None) is None


def test_patch_era_span_same_era_collapses():
    assert patch_era_span("2024-01-10", "2024-09-01") == "2024 (VCT)"


def test_patch_era_span_cross_era_widens():
    assert patch_era_span("2023-02-01", "2026-06-21") == (
        "2023 (Episodes 6-7) to 2026 (VCT)"
    )


def test_patch_era_span_missing_date_is_none():
    assert patch_era_span(None, "2024-01-01") is None
    assert patch_era_span("2024-01-01", None) is None
