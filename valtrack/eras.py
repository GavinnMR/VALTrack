"""Rough Valorant era labels for the patch-era banner.

The data carries no patch number, so the most honest thing is a rough reminder
of which competitive era a date falls in. These labels are deliberately coarse
(by year and Valorant episode grouping); they exist to remind the user that
all-time figures can span very different maps, agents, and metas, not to claim a
precise patch.
"""

# Coarse era label per calendar year. Years past the table fall back to a plain
# VCT-year label, so a new year does not need a code change to stay sensible.
_ERA_BY_YEAR = {
    2021: "2021 (Episodes 2-3)",
    2022: "2022 (Episodes 4-5)",
    2023: "2023 (Episodes 6-7)",
    2024: "2024 (VCT)",
    2025: "2025 (VCT)",
}


def era_label(date_str):
    """A rough era label for an ISO date string, or None when it is missing."""
    if not date_str:
        return None
    year = int(str(date_str)[:4])
    if year <= 2020:
        return "2020 (closed beta and launch)"
    return _ERA_BY_YEAR.get(year, f"{year} (VCT)")


def patch_era_span(start_date, end_date):
    """A rough description of the era span between two ISO dates.

    Returns a single era when both ends fall in the same one, an "X to Y" span
    when they differ, and None when either date is missing. This is what the
    banner shows, so the user sees at a glance how wide a meta range the figures
    cover.
    """
    start = era_label(start_date)
    end = era_label(end_date)
    if start is None or end is None:
        return None
    if start == end:
        return start
    return f"{start} to {end}"
