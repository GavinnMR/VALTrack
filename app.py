"""VALTrack: two-team comparison view with a shared date range.

Reads the stored teams, rosters, and matches from SQLite and shows two franchise
teams side by side. A single date-range control drives both teams: the record,
recent matches, and form and streak recompute for the chosen window, while the
ranking, rating, and earnings stay as VLR's current all-time snapshot. No score,
rating, or winner call is produced here; the user reads the figures and the gaps.

Run with: streamlit run app.py
"""
import datetime as dt

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from valtrack import (
    db, eras, freshness, ingest, journal, queries, schedule, stats, veto)
from valtrack.window import DateWindow, EventFilter, StageFilter, is_lan_event

# A team is flagged stale once this many days pass with no match.
STALE_DAYS = 42
# The stored data is flagged stale once this many days pass since the last
# refresh, nudging the user to pull new matches.
STALE_REFRESH_DAYS = 7
# Cap how many matches the in-app refresh details in one click, so the button
# stays quick and never turns into the multi-hour full harvest.
REFRESH_DETAIL_LIMIT = 100

st.set_page_config(page_title="VALTrack", layout="wide")

# Smallest sample still treated as solid for each kind of figure. Below these a
# statistic is flagged, because a rate over very few observations can swing on a
# single result. They are deliberately modest: a flag means "read with care",
# not "ignore". One map is about 24 rounds, so the round thresholds sit near a
# map or a half.
MIN_MATCHES = 5        # decided matches behind a record or form
MIN_MAP_ROUNDS = 24    # rounds behind a per-map side split
MIN_PISTOLS = 10       # pistol rounds behind the pistol rate
MIN_DUELS = 20         # opening duels behind an opening-duel rate
MIN_PLAYER_MAPS = 4    # maps behind a player's aggregated line
MIN_VETO_APPEAR = 5    # times a map was in the pool behind its veto rates

# How many of a player's most recent maps the recent-rating trajectory averages
# (P4). Small enough to catch a hot or cold streak, large enough to not be one map.
PLAYER_RECENT_MAPS = 10

FLAG = "⚠"        # the small-sample marker shown next to a thin figure

# Two color palettes for the win/loss and leader cues (item 13). The default
# leans on green and red; the colorblind-safe option swaps to blue and orange,
# which red-green colorblind users can still separate. Anywhere color carries
# meaning reads its colors from here so the toggle reaches all of them at once.
PALETTES = {
    "Default (green/red)": {"good": "#2a9d8f", "bad": "#e76f51", "lead": "#cdeee9"},
    "Colorblind-safe (blue/orange)": {
        "good": "#1f77b4", "bad": "#e08214", "lead": "#cfe2f3"},
}
DEFAULT_PALETTE = "Default (green/red)"


def palette():
    """The active color set, read from the palette toggle (item 13)."""
    return PALETTES.get(
        st.session_state.get("palette"), PALETTES["Default (green/red)"])

# Shown wherever a section has no per-match detail in the selected range.
DETAIL_EMPTY = (
    "No per-map detail stored in this range. Run the detail harvest "
    "(python harvest.py --pass details) to populate it."
)


def flag_if_small(n, threshold):
    """Return the warning marker when a count is a small sample, else blank."""
    return f" {FLAG}" if stats.is_small_sample(n, threshold) else ""


def rank_text(rank):
    """Show a rank, or an honest placeholder when we have none. Never a guess."""
    return f"#{rank}" if rank is not None else "not ranked"


def team_label(team):
    return f"{team['name']} ({team['league'].capitalize()})"


# --- cached reads -----------------------------------------------------------
# Every widget interaction reruns the whole script, which without caching reruns
# all of the SQL against a database headed for tens of MB. These wrappers cache
# the computed rows keyed by the query arguments. The connection is passed
# underscore-prefixed so Streamlit skips it when hashing (a live sqlite handle is
# neither hashable nor a sensible cache key), and a db_key is passed so two
# databases opened at different paths (as the tests do) never share a cache
# entry. run_incremental_refresh clears the cache so a refresh shows new data.
# The Row lists are turned into plain dicts so the cached values are simple and
# the render code, which reads rows by key, is unaffected.

def _db_key():
    """A stable key for the active database, read at call time so tests see it."""
    return str(db.DB_PATH)


def _dicts(rows):
    return [dict(r) for r in rows]


@st.cache_data(show_spinner=False)
def cq_roster(db_key, _conn, team_id):
    return _dicts(queries.get_roster(_conn, team_id))


# Every match-derived read takes the same three filters (date window, LAN/online
# event filter, group/playoff stage filter), so the cached wrappers carry all
# three and pass them through. They are hashable (frozen dataclasses), so they key
# the cache cleanly: a different stage never reuses another stage's rows.

@st.cache_data(show_spinner=False)
def cq_record(db_key, _conn, team_id, window, events, stage):
    return queries.team_record(_conn, team_id, window, events, stage)


@st.cache_data(show_spinner=False)
def cq_results(db_key, _conn, team_id, window, events, stage):
    return queries.decided_results(_conn, team_id, window, events, stage)


@st.cache_data(show_spinner=False)
def cq_recent(db_key, _conn, team_id, window, events, stage, limit):
    return queries.recent_matches(
        _conn, team_id, window, limit=limit, events=events, stage=stage)


@st.cache_data(show_spinner=False)
def cq_sos(db_key, _conn, team_id, window, events, stage):
    return queries.schedule_strength(_conn, team_id, window, events, stage)


@st.cache_data(show_spinner=False)
def cq_map_results(db_key, _conn, team_id, window, stage):
    return _dicts(queries.team_map_results(_conn, team_id, window, stage))


@st.cache_data(show_spinner=False)
def cq_rounds(db_key, _conn, team_id, window, stage):
    return _dicts(queries.team_rounds(_conn, team_id, window, stage))


@st.cache_data(show_spinner=False)
def cq_player_opening(db_key, _conn, team_id, window, stage):
    # Merge duplicate spellings of one player (item 7) before anything aggregates.
    return stats.merge_player_aliases(
        queries.team_player_opening(_conn, team_id, window, stage))


@st.cache_data(show_spinner=False)
def cq_player_stats(db_key, _conn, team_id, window, stage):
    return stats.merge_player_aliases(
        queries.team_player_stats(_conn, team_id, window, stage))


@st.cache_data(show_spinner=False)
def cq_compositions(db_key, _conn, team_id, window, stage):
    return _dicts(queries.team_compositions(_conn, team_id, window, stage))


@st.cache_data(show_spinner=False)
def cq_performance(db_key, _conn, team_id, window, stage):
    return _dicts(queries.team_performance(_conn, team_id, window, stage))


@st.cache_data(show_spinner=False)
def cq_win_types(db_key, _conn, team_id, window, stage):
    return _dicts(queries.team_round_win_types(_conn, team_id, window, stage))


@st.cache_data(show_spinner=False)
def cq_economy(db_key, _conn, team_id, window, stage):
    return _dicts(queries.team_economy(_conn, team_id, window, stage))


@st.cache_data(show_spinner=False)
def cq_map_opp_rank(db_key, _conn, team_id, window, stage):
    return _dicts(queries.team_map_opponent_rank(_conn, team_id, window, stage))


@st.cache_data(show_spinner=False)
def cq_appearances(db_key, _conn, team_id, window, stage):
    return _dicts(queries.player_appearances(_conn, team_id, window, stage))


@st.cache_data(show_spinner=False)
def cq_vetos(db_key, _conn, team_id, window):
    return _dicts(queries.team_vetos(_conn, team_id, window))


@st.cache_data(show_spinner=False)
def cq_series(db_key, _conn, team_id, window, stage):
    return _dicts(queries.team_series_results(_conn, team_id, window, stage))


@st.cache_data(show_spinner=False)
def cq_h2h(db_key, _conn, a_id, b_id, window, events, stage):
    return queries.head_to_head(_conn, a_id, b_id, window, events, stage)


@st.cache_data(show_spinner=False)
def cq_h2h_maps(db_key, _conn, a_id, b_id, window, events, stage):
    return _dicts(queries.head_to_head_maps(_conn, a_id, b_id, window, events, stage))


@st.cache_data(show_spinner=False)
def cq_common(db_key, _conn, a_id, b_id, window, events, stage):
    return queries.common_opponents(_conn, a_id, b_id, window, events, stage)


@st.cache_data(show_spinner=False)
def cq_coverage(db_key, _conn, team_id, window, events, stage):
    return queries.detail_coverage(_conn, team_id, window, events, stage)


@st.cache_data(show_spinner=False)
def cq_window_summary(db_key, _conn, team_id, window, events, stage):
    return queries.team_window_summary(_conn, team_id, window, events, stage)


@st.cache_data(show_spinner=False)
def cq_rich_coverage(db_key, _conn, team_id, window, stage):
    return queries.rich_coverage(_conn, team_id, window, stage)


@st.cache_data(show_spinner=False)
def cq_rest_load(db_key, _conn, team_id):
    return queries.team_rest_load(_conn, team_id)


@st.cache_data(show_spinner=False)
def cq_meeting_maps(db_key, _conn, match_id):
    return _dicts(queries.meeting_maps(_conn, match_id))


@st.cache_data(show_spinner=False)
def cq_meeting_lineup(db_key, _conn, match_id, team_name):
    return queries.meeting_lineup(_conn, match_id, team_name)


@st.cache_data(show_spinner=False)
def cq_map_pool(db_key, _conn):
    # The current rotation as a sorted tuple, so it is hashable and stable for the
    # cache. Read as a set wherever it is used.
    return tuple(sorted(queries.recent_map_pool(_conn)))


# --- current-map-pool guardrail (P2) ----------------------------------------
# Over a wide window, retired maps (Icebox, Sunset) and the junk "TBD" map leak
# into the map tables and the veto reconstruction. These two helpers gate which
# maps a map table shows: non-Valorant junk is always dropped, and an out-of-
# rotation map is either hidden (pool-only on) or marked (pool-only off).

ROTATED_OUT_MARK = " (rotated out)"


def map_visible(name, current_pool, pool_only):
    """Whether a map should appear in a map table at all.

    Drops anything that is not a real Valorant map (the junk "TBD", a null name).
    When the current-pool filter is on and a current pool is known, also drops a
    canonical map that has left rotation, so the table shows only playable maps.
    """
    if name not in veto.CANON_MAPS:
        return False
    if pool_only and current_pool and name not in current_pool:
        return False
    return True


def rotation_mark(name, current_pool):
    """A suffix marking a canonical map that is out of the current rotation.

    Blank when the map is current (or the pool is unknown), so a stale high win
    rate on a retired map is visible rather than reading as a current strength.
    """
    if current_pool and name in veto.CANON_MAPS and name not in current_pool:
        return ROTATED_OUT_MARK
    return ""


# --- small formatting helpers for the aligned and numeric tables ------------

def pct_num(rate):
    """A win rate (0..1) as a 0..100 number for a numeric column, or None.

    None renders as a blank cell and, unlike a pre-formatted string, sorts and
    data-bars numerically, which is the point of the aligned and sortable tables.
    """
    return 100 * rate if rate is not None else None


def gap_str(a, b, suffix="", decimals=0):
    """A signed gap (A minus B) as text, or a dash when either side is missing.

    The gap is the charter's "difference between them": a per-statistic delta,
    never a tally across categories. A blank side has nothing to subtract, so the
    honest cell is a dash rather than a fabricated zero.
    """
    if a is None or b is None:
        return "-"
    return f"{a - b:+.{decimals}f}{suffix}"


# Column setup for the per-player table: numeric columns so a click sorts by
# value rather than by the text of a pre-formatted string, the format kept on the
# column, a data bar on KAST so its magnitude is scannable, and a help tooltip on
# each abbreviation so a casual read does not need the glossary.
PLAYER_COLUMN_CONFIG = {
    "Rating": st.column_config.NumberColumn(
        "Rating", format="%.2f",
        help="VLR composite rating, round-weighted across the player's maps"),
    f"Rating L{PLAYER_RECENT_MAPS}": st.column_config.NumberColumn(
        f"Rating L{PLAYER_RECENT_MAPS}", format="%.2f",
        help=f"Round-weighted rating over the player's last {PLAYER_RECENT_MAPS} "
             "maps in range, so a hot or cold streak shows"),
    "Rating trend": st.column_config.NumberColumn(
        "Rating trend", format="%+.2f",
        help="Recent rating minus the window rating: positive means trending up"),
    "Rating range": st.column_config.TextColumn(
        "Rating range", help="Lowest to highest single-map rating in range"),
    "Rating sigma": st.column_config.NumberColumn(
        "Rating sigma", format="%.2f",
        help="Standard deviation of the rating across maps (consistency); higher "
             "means more feast-or-famine. Needs at least two maps."),
    "ACS": st.column_config.NumberColumn(
        "ACS", format="%.0f", help="Average combat score per round"),
    "K/D": st.column_config.NumberColumn(
        "K/D", format="%.2f", help="Kills divided by deaths"),
    "KAST": st.column_config.ProgressColumn(
        "KAST", format="%.0f%%", min_value=0, max_value=100,
        help="Percent of rounds with a kill, assist, survival, or trade"),
    "ADR": st.column_config.NumberColumn(
        "ADR", format="%.0f", help="Average damage per round"),
    "KPR": st.column_config.NumberColumn(
        "KPR", format="%.2f", help="Kills per round"),
    "APR": st.column_config.NumberColumn(
        "APR", format="%.2f", help="Assists per round"),
    "HS%": st.column_config.NumberColumn(
        "HS%", format="%.0f%%",
        help="Headshot percentage (round-weighted approximation)"),
    "FKPR": st.column_config.NumberColumn(
        "FKPR", format="%.2f", help="First kills per round"),
    "FDPR": st.column_config.NumberColumn(
        "FDPR", format="%.2f", help="First deaths per round"),
    "Maps": st.column_config.NumberColumn(
        "Maps", help="Maps played in range (sample size)"),
    "Rounds": st.column_config.NumberColumn(
        "Rounds", help="Rounds played in range (sample size)"),
}


def _roster_change_start(conn, team_a_id, team_b_id):
    """The window start for the "since last roster change" preset, or None.

    A shared window drives both teams, so this takes the more recent of the two
    teams' approximate last-change dates: the window then covers a span where both
    teams' current fives were in place, which is the lineup the user actually cares
    about. The change date is approximate (derived from the appearance timeline,
    since the transactions endpoint is unreliable), so the caller labels it so.
    Returns None when neither team has a derivable change date.
    """
    k = _db_key()
    dates = []
    for tid in (team_a_id, team_b_id):
        five = stats.current_five_names(cq_roster(k, conn, tid))
        d = queries.last_roster_change_date(conn, tid, five)
        if d:
            dates.append(d)
    if not dates:
        return None
    return dt.date.fromisoformat(max(dates))


def choose_window(conn, team_a_id=None, team_b_id=None):
    """Render the shared date-range control and return a DateWindow.

    All time is the default and applies no filter. A custom range is bounded by
    the earliest and latest stored match dates. The presets are competitive units
    rather than arbitrary spans: the recent rolling windows, a rough current-split
    window, and "since last roster change", which ties the stats to the lineup
    that will actually play. The same window drives both teams, so the comparison
    stays aligned.
    """
    mn, mx = queries.match_date_bounds(conn)
    today = dt.date.today()
    mode = st.radio(
        "Date range",
        WINDOW_MODES,
        horizontal=True,
        key="dwmode",
        help=(
            "Windowed figures (record, recent matches, form and streak, and the "
            "detail splits) recompute for the chosen range. The presets are quick "
            "competitive spans; pick Custom range for an exact window. Ranking, "
            "rating, and earnings are VLR's current all-time values and do not "
            "change."
        ),
    )
    if mode == "All time" or mn is None:
        return DateWindow.all_time()
    if mode == "Last 3 months":
        return DateWindow(today - dt.timedelta(days=90), today)
    if mode == "Last 6 months":
        return DateWindow(today - dt.timedelta(days=180), today)
    if mode == "Year to date":
        return DateWindow(dt.date(today.year, 1, 1), today)
    if mode == "Current split (approx)":
        # No split metadata is stored, so this is a pragmatic recent-competition
        # window (about four months), labeled approximate rather than implying an
        # exact split boundary.
        st.caption(
            "Approximate: no split boundary is stored, so this is a rough recent "
            "window (about four months), not an exact competitive split.")
        return DateWindow(today - dt.timedelta(days=120), today)
    if mode == "Since last roster change":
        start = _roster_change_start(conn, team_a_id, team_b_id)
        if start is None:
            st.caption(
                "No roster-change date could be derived from appearances, so this "
                "falls back to all time.")
            return DateWindow.all_time()
        st.caption(
            f"Approximate: starts {start.isoformat()}, the more recent of the two "
            "teams' last roster changes, derived from who has actually played "
            "(the transactions feed is unreliable), so treat it as a rough date.")
        return DateWindow(start, today)

    min_d = dt.date.fromisoformat(mn)
    max_d = dt.date.fromisoformat(mx)
    picked = st.date_input(
        "Custom range",
        value=(min_d, max_d),
        min_value=min_d,
        max_value=max_d,
        key="dwrange",
    )
    if isinstance(picked, (tuple, list)) and len(picked) == 2:
        return DateWindow(picked[0], picked[1])
    st.caption("Pick both a start and an end date to apply the range.")
    return DateWindow.all_time()


def render_form_sparkline(results, key=None):
    """A small Plotly sparkline of the running win-loss differential.

    `results` is the decided results newest first. We chart the last stretch in
    chronological order as a cumulative net (each win +1, each loss -1), so an
    upward line is a team trending up. It is a shape, not a number, which is the
    point of a sparkline. A unique key avoids a duplicate-element clash when two
    teams (or two views) chart the same shape.
    """
    recent = list(reversed(results[:15]))  # oldest to newest for the trend
    net, series = 0, []
    for r in recent:
        net += 1 if r == "W" else -1
        series.append(net)
    fig = go.Figure(go.Scatter(y=series, mode="lines+markers"))
    fig.update_layout(
        height=120,
        margin=dict(l=0, r=0, t=0, b=0),
        showlegend=False,
        xaxis=dict(visible=False),
        yaxis=dict(title="net W-L", zeroline=True),
    )
    st.plotly_chart(fig, width="stretch", key=key)


def color_form(results):
    """Recent results as colored letters, a win in the good color, a loss bad.

    Uses Streamlit's named markdown colors so the colorblind-safe palette (item
    13) swaps green/red for blue/orange here too.
    """
    cb = str(st.session_state.get("palette", "")).startswith("Colorblind")
    good = "blue" if cb else "green"
    bad = "orange" if cb else "red"
    return " ".join(
        f":{good}[{r}]" if r == "W" else f":{bad}[{r}]" for r in results
    )


def render_record_and_form(conn, team, window, events, stage):
    k = _db_key()
    record = cq_record(k, conn, team["id"], window, events, stage)
    if record["decided"]:
        winpct = f"{100 * record['wins'] / record['decided']:.0f}%"
    else:
        winpct = "n/a"
    st.metric(f"Record ({window.label})", f"{record['wins']}-{record['losses']}")
    flag = flag_if_small(record["decided"], MIN_MATCHES)
    ci = ci_text(record["wins"], record["decided"])
    ci_note = f". {ci}" if ci else ""
    st.caption(f"{record['decided']} decided matches, win rate {winpct}{flag}{ci_note}")

    sos = cq_sos(k, conn, team["id"], window, events, stage)
    if sos["ranked"]:
        st.caption(
            f"Strength of schedule: average opponent rank about #"
            f"{sos['avg_opp_rank']:.0f} over {sos['ranked']} ranked opponents "
            f"(of {sos['decided']} decided). Ranks are VLR's current snapshot and "
            "only stored teams carry one, so this is a rough signal."
        )
    elif sos["decided"]:
        st.caption(
            f"Strength of schedule: none of the {sos['decided']} opponents in this "
            "range have a stored rank, so an average is not shown."
        )

    results = cq_results(k, conn, team["id"], window, events, stage)
    fs = stats.form_and_streak(results)
    if fs["decided"]:
        st.markdown("**Form** (most recent first): " + color_form(fs["form"]))
        st.write(f"**Current streak:** {fs['streak_kind']}{fs['streak_len']}")
        render_form_sparkline(results, key=f"spark_form_{team['id']}")
    else:
        st.write("**Form:** no decided matches in this range")


def render_snapshot(team):
    st.divider()
    st.caption("Current snapshot from VLR (all-time, not affected by the date range)")
    c1, c2 = st.columns(2)
    c1.metric("Regional rank", rank_text(team["regional_rank"]))
    c2.metric("World rank", rank_text(team["world_rank"]))
    if team["rating"] and team["rating"] != "N/A":
        st.metric("Rating", team["rating"])
    earnings = team["total_winnings"] or team["earnings"]
    if earnings:
        st.metric("Total winnings", earnings)
    st.caption("Event placements are not harvested yet.")


def pct(rate):
    """A win rate (0..1) as a whole-percent string, or a dash when unknown."""
    return f"{100 * rate:.0f}%" if rate is not None else "-"


def ci_text(won, total):
    """A Wilson 95% confidence band for a rate, as text, or blank (item 4).

    Quantifies how thin a small sample is instead of just flagging it: a 60% over
    10 rounds comes back as a wide band, a 60% over 200 as a tight one. Blank when
    there is nothing to judge.
    """
    iv = stats.wilson_interval(won, total)
    if iv is None:
        return ""
    return f"95% CI {100 * iv[0]:.0f} to {100 * iv[1]:.0f}%"


def render_map_splits(conn, team, window, stage, highlight=None,
                      current_pool=None, pool_only=False):
    """Per-map win rate with attack and defense side splits for the window.

    Computed from the stored rounds, so it only has figures for maps whose
    per-match detail has been harvested. When none is stored for this team in the
    range, say so plainly rather than show an empty table. Maps in the
    likely-played pool are marked with a star (item 20) so the relevant maps draw
    the eye in every map table, not just the veto section. Junk maps are dropped,
    and an out-of-rotation map is hidden when the current-pool filter is on or
    marked when it is off (P2).
    """
    st.divider()
    st.subheader("Per-map and side win rates")
    highlight = highlight or set()
    k = _db_key()
    map_rows = cq_map_results(k, conn, team["id"], window, stage)
    round_rows = cq_rounds(k, conn, team["id"], window, stage)
    table = stats.per_map_splits(map_rows, round_rows, team["name"])
    table = [m for m in table
             if map_visible(m["map_name"], current_pool, pool_only)]
    if not table:
        st.caption(DETAIL_EMPTY if not pool_only else
                   "No current-rotation maps with detail in this range.")
        return
    rows = []
    for m in table:
        decided = m["won"] + m["lost"]
        flag = flag_if_small(m["rounds_total"], MIN_MAP_ROUNDS)
        star = "★ " if m["map_name"] in highlight else ""
        rows.append({
            "Map": star + m["map_name"] + rotation_mark(m["map_name"], current_pool)
                   + flag,
            "Maps": f"{m['won']}-{m['lost']}",
            "Map win%": pct_num(m["map_winrate"]) if decided else None,
            "ATK win%": pct_num(m["atk_winrate"]),
            "ATK rounds": m["atk_total"],
            "DEF win%": pct_num(m["def_winrate"]),
            "DEF rounds": m["def_total"],
        })
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        column_config={
            "Map win%": st.column_config.ProgressColumn(
                "Map win%", format="%.0f%%", min_value=0, max_value=100,
                help="Win rate over decided maps",
            ),
            "ATK win%": st.column_config.ProgressColumn(
                "ATK win%", format="%.0f%%", min_value=0, max_value=100,
                help="Attack-side round win rate",
            ),
            "DEF win%": st.column_config.ProgressColumn(
                "DEF win%", format="%.0f%%", min_value=0, max_value=100,
                help="Defense-side round win rate",
            ),
        },
    )

    chart = [m for m in table
             if m["atk_winrate"] is not None or m["def_winrate"] is not None]
    if chart:
        names = [m["map_name"] for m in chart]
        fig = go.Figure()
        fig.add_bar(
            name="ATK",
            x=names,
            y=[100 * m["atk_winrate"] if m["atk_winrate"] is not None else None
               for m in chart],
        )
        fig.add_bar(
            name="DEF",
            x=names,
            y=[100 * m["def_winrate"] if m["def_winrate"] is not None else None
               for m in chart],
        )
        fig.update_layout(
            barmode="group",
            height=260,
            margin=dict(l=0, r=0, t=10, b=0),
            yaxis=dict(title="win %", range=[0, 100]),
            legend=dict(orientation="h", y=1.1),
        )
        st.plotly_chart(fig, width="stretch", key=f"mapchart_{team['id']}")

    rot_note = (
        f' A map marked "{ROTATED_OUT_MARK.strip()}" has left the current '
        "rotation, so its win rate is frozen on a map that cannot be played now."
        if current_pool and any(rotation_mark(m["map_name"], current_pool)
                                for m in table) else ""
    )
    st.caption(
        f"Map win% is over decided maps. Side win rates are over rounds played "
        f"on that side. Round and map counts are shown so a small sample is "
        f"visible; {FLAG} marks a map with fewer than {MIN_MAP_ROUNDS} rounds."
        + rot_note
    )
    note = _map_opp_rank_note(conn, team, window, stage)
    if note:
        st.caption(note)
    render_tier_split(team, map_rows, round_rows)


def render_tier_split(team, map_rows, round_rows):
    """Map, round, and pistol win rate split by opponent strength tier (item 5).

    Reveals "beats weak teams, struggles against elite" at a glance, the core
    uncertainty in a cross-region matchup where the two teams share few opponents.
    Reuses the opponent rank already carried on the map and round rows; an opponent
    with no stored rank falls into the weakest tier. It re-presents reachable data
    by opponent strength and never calls a winner.
    """
    map_buckets = stats.partition_by_tier(map_rows)
    round_buckets = stats.partition_by_tier(round_rows)
    if not any(map_buckets.get(t) for t in stats.TIER_ORDER):
        return
    with st.expander("Split by opponent tier"):
        table = []
        for tier in stats.TIER_ORDER:
            mb = map_buckets.get(tier, [])
            rb = round_buckets.get(tier, [])
            mw = stats.map_winrates(mb, team["name"])
            won = sum(a["won"] for a in mw.values())
            lost = sum(a["lost"] for a in mw.values())
            decided = won + lost
            rwon = sum(1 for r in rb if r["winner_team"] == team["name"]
                       and r["winner_side"] in ("atk", "def"))
            rtot = sum(1 for r in rb if r["winner_side"] in ("atk", "def"))
            p = stats.pistol_winrate(rb, team["name"])
            table.append({
                "Opponent tier": stats.TIER_LABELS[tier],
                "Maps": f"{won}-{lost}",
                "Map win%": pct_num(won / decided) if decided else None,
                "Round win%": pct_num(rwon / rtot) if rtot else None,
                "Pistol%": pct_num(p["winrate"]),
            })
        st.dataframe(
            pd.DataFrame(table), hide_index=True,
            column_config={
                c: st.column_config.NumberColumn(c, format="%.0f%%")
                for c in ("Map win%", "Round win%", "Pistol%")},
        )
        st.caption(
            "The same figures split by opponent regional rank: top 10, 11-30, and "
            "31-plus or unranked. Ranks are VLR's current snapshot applied to past "
            "matches, so this is a rough cut; an unranked opponent (mostly "
            "non-franchise) falls into the last tier. Each tier stands alone."
        )


def render_pistol(conn, team, window, stage):
    """Team-level pistol-round win rate with attack and defense splits.

    Computed from the stored rounds (round 1 and round 13 of each map), so it
    only has figures where per-match detail has been harvested. The won/total
    sample is shown so a thin pistol sample is visible. Economy conversion (eco
    and anti-eco) is not shown: the data source returns broken per-map economy,
    so those figures are deferred rather than guessed.
    """
    st.divider()
    st.subheader("Pistol rounds")
    round_rows = cq_rounds(_db_key(), conn, team["id"], window, stage)
    p = stats.pistol_winrate(round_rows, team["name"])
    if p["total"] == 0:
        st.caption(DETAIL_EMPTY)
        return
    overall, atk, defense = st.columns(3)
    overall.metric(
        "Pistol win%", pct(p["winrate"]),
        help=f"{p['won']} of {p['total']}. {ci_text(p['won'], p['total'])}")
    atk.metric(
        "ATK pistol%", pct(p["atk_winrate"]), help=f"{p['atk_won']} of {p['atk_total']}"
    )
    defense.metric(
        "DEF pistol%", pct(p["def_winrate"]), help=f"{p['def_won']} of {p['def_total']}"
    )
    small = flag_if_small(p["total"], MIN_PISTOLS)
    st.caption(
        f"Pistol win rate over {p['total']} pistol rounds{small} (round 1 and "
        f"round 13 of each map){'; small sample' if small else ''}. The overall "
        f"rate's confidence band ({ci_text(p['won'], p['total'])}) shows how much "
        "the sample pins it down: a wide band means read it with care. Eco and "
        "anti-eco conversion are not shown: the data source returns broken per-map "
        "economy, so those figures are deferred until it is fixed."
    )

    # Round-after-pistol conversion: the reliable salvage of the economy feature.
    # The round right after a pistol is in practice the bonus / anti-eco round, so
    # its result captures most of what economy conversion meant to show, from the
    # rounds table that is already correct.
    pp = stats.post_pistol_conversion(round_rows, team["name"])
    if pp["won_pistols"] or pp["lost_pistols"]:
        st.markdown("**Next round after a pistol**")
        conv, recover = st.columns(2)
        conv.metric(
            "After winning the pistol", pct(pp["won_conv_rate"]),
            help=f"Won the next round {pp['won_then_won']} of {pp['won_pistols']} "
                 "times after taking the pistol (the conversion / snowball).")
        recover.metric(
            "After losing the pistol", pct(pp["lost_recover_rate"]),
            help=f"Won the next round {pp['lost_then_won']} of {pp['lost_pistols']} "
                 "times despite dropping the pistol (the break / recovery).")
        st.caption(
            "Win rate of the round immediately after a pistol (round 2 and round "
            "14), split by whether the team won or lost the pistol. This is a "
            "proxy for economy conversion from reliable round data, not true eco "
            "conversion: without buy types a forced-buy upset looks the same as a "
            "clean conversion, so read it as 'next round after pistol', not eco."
        )


def render_opening(conn, team, window, stage, five_names=None):
    """Team and per-player opening-duel win rates with attack and defense splits.

    Computed from the per-map first-kill and first-death counts in the stored
    detail, so it only has figures where per-match detail has been harvested. The
    counts are per-map totals, not per-round events, so the split is over the
    opening duels taken on each side rather than a round-by-round timeline. The
    duel counts are shown so a thin sample stays visible. When five_names is set
    the figures are narrowed to the current five.
    """
    st.divider()
    st.subheader("Opening duels")
    rows = stats.keep_players(
        cq_player_opening(_db_key(), conn, team["id"], window, stage), five_names
    )
    o = stats.opening_duels(rows, team["name"])
    if o["duels"] == 0:
        st.caption(DETAIL_EMPTY)
        return
    overall, atk, defense = st.columns(3)
    overall.metric(
        "Opening-duel win%", pct(o["winrate"]),
        help=f"{o['fk']} first kills of {o['duels']} opening duels. "
             f"{ci_text(o['fk'], o['duels'])}",
    )
    atk.metric(
        "ATK opening%", pct(o["atk_winrate"]),
        help=f"{o['atk_fk']} of {o['atk_duels']}",
    )
    defense.metric(
        "DEF opening%", pct(o["def_winrate"]),
        help=f"{o['def_fk']} of {o['def_duels']}",
    )
    player_rows = []
    for p in o["players"]:
        player_rows.append({
            "Player": p["player_name"] + flag_if_small(p["duels"], MIN_DUELS),
            "FK": p["fk"],
            "FD": p["fd"],
            "Duels": p["duels"],
            "Win%": pct_num(p["winrate"]),
            "ATK%": pct_num(p["atk_winrate"]),
            "DEF%": pct_num(p["def_winrate"]),
        })
    st.dataframe(
        pd.DataFrame(player_rows),
        hide_index=True,
        column_config={
            "Win%": st.column_config.NumberColumn(
                "Win%", format="%.0f%%", help="First kills over opening duels"),
            "ATK%": st.column_config.NumberColumn("ATK%", format="%.0f%%"),
            "DEF%": st.column_config.NumberColumn("DEF%", format="%.0f%%"),
            "FK": st.column_config.NumberColumn(help="First kills"),
            "FD": st.column_config.NumberColumn(help="First deaths"),
        },
    )
    team_small = flag_if_small(o["duels"], MIN_DUELS)
    ci = ci_text(o["fk"], o["duels"])
    ci_note = f" The overall rate's confidence band is {ci}." if ci else ""
    st.caption(
        f"Opening-duel win rate is first kills over opening duels (first kills "
        f"plus first deaths). The attack and defense splits are per-side totals, "
        f"not a round-by-round timeline, since the source stores only per-map "
        f"first-kill and first-death counts. Duel counts are shown so a small "
        f"sample is visible; {FLAG} marks fewer than {MIN_DUELS} duels"
        f"{' (team total included)' if team_small else ''}.{ci_note}"
    )


def num1(value):
    """A stat to one decimal, or a dash when we have nothing to show."""
    return f"{value:.1f}" if value is not None else "-"


def num2(value):
    """A stat to two decimals (per-round figures), or a dash when unknown."""
    return f"{value:.2f}" if value is not None else "-"


def pct100(value):
    """A 0..100 percentage stat as a whole-percent string, or a dash."""
    return f"{value:.0f}%" if value is not None else "-"


def render_player_stats(conn, team, window, stage, five_names=None):
    """Per-player aggregated statistics for the window, with a per-agent view.

    Computed from the stored per-map player lines, so it only has figures where
    per-match detail has been harvested. The rate stats (rating, ACS, ADR, KAST,
    headshot percentage) are round-weighted across the player's maps; K/D and the
    per-round figures are summed then divided. Maps and rounds are shown as the
    sample size. The rating column carries the per-map spread (item: player
    consistency) so a steady and a swingy player are distinguishable. When
    five_names is set the table is narrowed to the current five.
    """
    st.divider()
    st.subheader("Player statistics")
    rows = stats.keep_players(
        cq_player_stats(_db_key(), conn, team["id"], window, stage), five_names
    )
    players = stats.player_aggregates(rows, team["name"])
    if not players:
        st.caption(DETAIL_EMPTY)
        return
    # Each player's rating over their most recent maps, so a star heating up or
    # cooling off is a number beside the window average, not buried in it (P4).
    recent = stats.player_recent_ratings(rows, team["name"], PLAYER_RECENT_MAPS)
    table = []
    for p in players:
        sp = p["rating_spread"]
        rec = recent.get(p["player_name"], {})
        rec_rating = rec.get("recent_rating")
        delta = (rec_rating - p["rating"]
                 if rec_rating is not None and p["rating"] is not None else None)
        table.append({
            "Player": p["player_name"] + flag_if_small(p["maps"], MIN_PLAYER_MAPS),
            "Rating": p["rating"],
            f"Rating L{PLAYER_RECENT_MAPS}": rec_rating,
            "Rating trend": delta,
            # Per-map spread of the rating, so a steady 1.10 and a swingy 1.10 read
            # differently (item: player consistency). The range is the low-high
            # band; sigma is the standard deviation across maps.
            "Rating range": (f"{sp['min']:.2f}-{sp['max']:.2f}"
                             if sp["min"] is not None else "-"),
            "Rating sigma": sp["std"],
            "ACS": p["acs"],
            "K/D": p["kd"],
            "KAST": p["kast"],
            "ADR": p["adr"],
            "KPR": p["kpr"],
            "APR": p["apr"],
            "HS%": p["hs_pct"],
            "FKPR": p["fk_per_round"],
            "FDPR": p["fd_per_round"],
            "Maps": p["maps"],
            "Rounds": p["rounds"],
        })
    st.dataframe(
        pd.DataFrame(table), hide_index=True, column_config=PLAYER_COLUMN_CONFIG
    )
    with st.expander("Agent pool and per-agent performance"):
        for p in players:
            if not p["agents"]:
                continue
            st.caption(p["player_name"])
            agent_rows = []
            for a in p["agents"]:
                agent_rows.append({
                    "Agent": a["agent"],
                    "Maps": a["maps"],
                    "Rating": a["rating"],
                    "ACS": a["acs"],
                    "K/D": a["kd"],
                })
            st.dataframe(
                pd.DataFrame(agent_rows),
                hide_index=True,
                column_config={
                    "Rating": st.column_config.NumberColumn(format="%.2f"),
                    "ACS": st.column_config.NumberColumn(format="%.0f"),
                    "K/D": st.column_config.NumberColumn(format="%.2f"),
                },
            )
    st.caption(
        "Rating, ACS, ADR, KAST, and HS% are round-weighted averages across the "
        "player's maps; K/D, KPR, APR, and the first-kill and first-death per-round "
        "rates are totals over the rounds played. HS% is round-weighted as an "
        "approximation, since the source stores only the per-map percentage. The "
        "rating range and sigma show per-map spread, so a steady performer and a "
        "feast-or-famine one are distinguishable; sigma needs at least two maps. "
        f"Rating L{PLAYER_RECENT_MAPS} is the same rating over only the player's "
        f"last {PLAYER_RECENT_MAPS} maps and the trend is that minus the window "
        "rating, so a player heating up or cooling off is visible as a direction, "
        f"not just an average. Maps and rounds are shown so a small sample is "
        f"visible; {FLAG} marks fewer than {MIN_PLAYER_MAPS} maps. Clutch "
        "statistics are not available from the data source."
    )


def _pvp_side(player):
    """The comparable headline figures for one side of a role pairing.

    Returns blanks when the slot is empty (one team has fewer players in the
    role), so the row still lines up.
    """
    if player is None:
        return {"name": "", "rating": "-", "acs": "-", "kd": "-",
                "kast": "-", "open": "-"}
    return {
        "name": player["player_name"] + flag_if_small(player["maps"], MIN_PLAYER_MAPS),
        "rating": num2(player["rating"]),
        "acs": num1(player["acs"]),
        "kd": num2(player["kd"]),
        "kast": pct100(player["kast"]),
        "open": pct(player["open_winrate"]),
    }


def _team_map_splits(conn, team, window, stage):
    """Per-map splits for a team keyed by map name, for the win-rate payoff.

    Non-Valorant junk (the "TBD" placeholder, a null map name) is dropped here so
    every consumer (veto, duel board, breakdown, aligned) is clear of it (P2).
    """
    k = _db_key()
    table = stats.per_map_splits(
        cq_map_results(k, conn, team["id"], window, stage),
        cq_rounds(k, conn, team["id"], window, stage),
        team["name"],
    )
    return {m["map_name"]: m for m in table if m["map_name"] in veto.CANON_MAPS}


def _map_recency(conn, team, window, stage):
    """Per-map last-played date and decided-map count for a team, keyed by map.

    Tells the user how fresh a per-map win-rate sample is, so a high rate on a map
    the team has not touched since a prior patch can be flagged. Reads the same
    map rows the win rates come from, scoped to maps the team actually played.
    """
    rows = cq_map_results(_db_key(), conn, team["id"], window, stage)
    out = {}
    for r in rows:
        name = r["map_name"]
        if not name:
            continue
        agg = out.setdefault(name, {"last": None, "maps": 0})
        if r["winner_name"] is not None:
            agg["maps"] += 1
        d = r["match_date"]
        if d and (agg["last"] is None or d > agg["last"]):
            agg["last"] = d
    return out


def _months_ago(date_str):
    """Whole months between an ISO date and today, or None when missing."""
    if not date_str:
        return None
    days = (dt.date.today() - dt.date.fromisoformat(date_str)).days
    return max(0, days // 30)


def render_veto_reconstruction(conn, team_a, team_b, window, stage,
                               current_pool=None, pool_only=False):
    """Reconstruct the likely map pool for the two teams and show map win rates.

    Aggregates each team's veto tendencies over the window, infers the active map
    pool, and reconstructs the probable picks, decider, and bans. For the maps
    likely to be played it then surfaces each team's map win rate with attack and
    defense side splits. This is built from veto history, not
    a real upcoming veto, and it makes no claim about who wins the match. When the
    current-pool filter is on, a map that has left rotation is dropped from the
    reconstruction; when off, it is still marked, so an all-time window does not
    quietly reconstruct a veto around a map that cannot be played now (P2).
    """
    st.header("Veto and map-pool reconstruction")
    k = _db_key()
    a_tend = veto.team_tendencies(
        cq_vetos(k, conn, team_a["id"], window), team_a["tag"], team_a["name"]
    )
    b_tend = veto.team_tendencies(
        cq_vetos(k, conn, team_b["id"], window), team_b["tag"], team_b["name"]
    )
    pool = veto.active_pool(a_tend, b_tend)
    if pool_only and current_pool:
        pool = [m for m in pool if m in current_pool]
    if not pool:
        st.caption(
            "No current-rotation maps in the veto history for this range."
            if pool_only else
            "No veto data stored in this range for these teams. Run the detail "
            "harvest (python harvest.py --pass details) to populate it."
        )
        return
    rec = veto.reconstruct(a_tend, b_tend, pool)

    a_pick = rec["a_pick"] or "-"
    b_pick = rec["b_pick"] or "-"
    decider = rec["decider"] or "-"
    c1, c2, c3 = st.columns(3)
    c1.metric(f"{team_a['name']} likely pick", a_pick)
    c2.metric(f"{team_b['name']} likely pick", b_pick)
    c3.metric("Probable decider", decider)
    if rec["likely_bans"]:
        st.caption("Likely bans: " + ", ".join(rec["likely_bans"]))

    tags = {}
    if rec["a_pick"]:
        tags[rec["a_pick"]] = f"{team_a['tag'] or 'A'} pick"
    if rec["b_pick"]:
        tags.setdefault(rec["b_pick"], f"{team_b['tag'] or 'B'} pick")
    if rec["decider"]:
        tags.setdefault(rec["decider"], "decider")
    pool_rows = []
    for r in rec["rows"]:
        seen = (r["a_appearances"] or 0) + (r["b_appearances"] or 0)
        pool_rows.append({
            "Map": r["map"] + rotation_mark(r["map"], current_pool)
                   + flag_if_small(seen, MIN_VETO_APPEAR),
            "Likely": tags.get(r["map"], "ban"),
            f"{team_a['tag'] or 'A'} pick%": pct(r["a_pick_rate"]),
            f"{team_a['tag'] or 'A'} ban%": pct(r["a_ban_rate"]),
            f"{team_b['tag'] or 'B'} pick%": pct(r["b_pick_rate"]),
            f"{team_b['tag'] or 'B'} ban%": pct(r["b_ban_rate"]),
            "Play likelihood": f"{r['play_score']:+.2f}",
        })
    st.dataframe(pd.DataFrame(pool_rows), hide_index=True)

    def role_color(map_name):
        label = tags.get(map_name, "ban")
        if "pick" in label:
            return "#2a9d8f"   # a team's likely pick
        if label == "decider":
            return "#e9c46a"   # probable decider
        return "#b9b9b9"       # likely ban

    chart_maps = [r["map"] for r in rec["rows"]]
    fig = go.Figure(go.Bar(
        x=[r["play_score"] for r in rec["rows"]],
        y=chart_maps,
        orientation="h",
        marker_color=[role_color(m) for m in chart_maps],
    ))
    fig.update_layout(
        height=max(180, 30 * len(chart_maps)),
        margin=dict(l=0, r=0, t=10, b=0),
        xaxis_title="play likelihood (pick rate minus ban rate, both teams)",
        yaxis=dict(autorange="reversed"),
    )
    st.plotly_chart(fig, width="stretch", key="vetochart")

    st.subheader("Win rates on the likely-played maps")
    a_splits = _team_map_splits(conn, team_a, window, stage)
    b_splits = _team_map_splits(conn, team_b, window, stage)
    if not a_splits and not b_splits:
        st.caption(
            "No per-map detail stored in this range yet, so map win rates are not "
            "available. They fill in as the detail harvest runs."
        )
    else:
        def split_cells(splits, map_name):
            m = splits.get(map_name)
            if not m:
                return ("-", "-", "-")
            decided = m["won"] + m["lost"]
            win = pct(m["map_winrate"]) if decided else "-"
            return (f"{win} ({m['won']}-{m['lost']})",
                    pct(m["atk_winrate"]), pct(m["def_winrate"]))

        a_rec = _map_recency(conn, team_a, window, stage)
        b_rec = _map_recency(conn, team_b, window, stage)
        a_tag, b_tag = team_a["tag"] or "A", team_b["tag"] or "B"

        def recency_cell(rec_map, map_name):
            r = rec_map.get(map_name)
            if not r or not r["maps"]:
                return "no maps"
            months = _months_ago(r["last"])
            ago = "this month" if months == 0 else (
                f"{months} mo ago" if months is not None else "unknown")
            return f"{r['maps']} maps, last {ago}"

        win_rows = []
        for map_name in rec["likely_played"]:
            a_win, a_atk, a_def = split_cells(a_splits, map_name)
            b_win, b_atk, b_def = split_cells(b_splits, map_name)
            win_rows.append({
                "Map": map_name,
                f"{a_tag} map%": a_win,
                f"{a_tag} ATK": a_atk,
                f"{a_tag} DEF": a_def,
                f"{a_tag} sample": recency_cell(a_rec, map_name),
                f"{b_tag} map%": b_win,
                f"{b_tag} ATK": b_atk,
                f"{b_tag} DEF": b_def,
                f"{b_tag} sample": recency_cell(b_rec, map_name),
            })
        st.dataframe(pd.DataFrame(win_rows), hide_index=True)

    st.caption(
        "Reconstructed from each team's veto history in the selected range, not "
        "an actual upcoming veto. The pool is inferred from the maps seen most in "
        "that history (narrow the date range for the current rotation). Play "
        "likelihood is each team's pick rate minus ban rate, summed; it ranks "
        f"maps, it does not predict the match winner. Pick and ban rates are over "
        f"the matches each map was in the pool; {FLAG} marks a map seen in fewer "
        f"than {MIN_VETO_APPEAR} of the two teams' vetos combined. The sample "
        "columns show how many maps and how recent each win-rate rests on, so a "
        "high rate on a map a team has not touched in months is visible."
    )

    render_veto_simulator(team_a, team_b, pool, a_splits, b_splits)

    st.divider()
    render_overlap(conn, team_a, team_b, window, stage, pool)


def render_veto_simulator(team_a, team_b, pool, a_splits, b_splits):
    """A manual what-if veto: the user drives the bans and picks, the data responds.

    The automatic reconstruction is one guess from tendency, but real vetos deviate
    with scouting and series context. This hands the veto to the user: pick the
    banned and picked maps and watch the leftover decider and the side splits for
    the chosen maps update. It is the tool's principle in its purest form, the user
    drives and the data responds, and it inherits the same staleness and
    small-sample caveats as the win rates above.
    """
    with st.expander("Manual veto (what-if)"):
        st.caption(
            "Drive the veto yourself: choose the banned and picked maps and the "
            "leftover becomes the decider. The side splits below update for the "
            "maps in play. This is a what-if over the same win-rate samples, not a "
            "prediction of the real veto."
        )
        a_tag, b_tag = team_a["tag"] or "A", team_b["tag"] or "B"
        # The pool changes when the teams or window change, so drop any stored
        # selection that is no longer a legal option before the widgets read it;
        # otherwise Streamlit rejects a session value outside the options.
        if "sim_bans" in st.session_state:
            st.session_state["sim_bans"] = [
                m for m in st.session_state["sim_bans"] if m in pool]
        banned = st.multiselect(
            "Banned maps", pool, key="sim_bans",
            help="Maps removed by either team in your what-if veto.")
        pickable = [m for m in pool if m not in banned]
        if "sim_picks" in st.session_state:
            st.session_state["sim_picks"] = [
                m for m in st.session_state["sim_picks"] if m in pickable]
        picked = st.multiselect(
            "Picked maps", pickable, key="sim_picks",
            help="Maps a team picks to play.")
        leftover = [m for m in pool if m not in banned and m not in picked]
        in_play = picked + leftover
        if leftover:
            st.caption("Leftover (likely decider): " + ", ".join(leftover))
        if not in_play:
            st.caption("Every map is banned, so there is nothing left to play.")
            return

        def cell(splits, name):
            m = splits.get(name)
            if not m:
                return ("-", "-", "-")
            decided = m["won"] + m["lost"]
            return (pct(m["map_winrate"]) if decided else "-",
                    pct(m["atk_winrate"]), pct(m["def_winrate"]))

        rows = []
        for name in in_play:
            a_win, a_atk, a_def = cell(a_splits, name)
            b_win, b_atk, b_def = cell(b_splits, name)
            rows.append({
                "Map": name + (" (decider)" if name in leftover and picked else ""),
                f"{a_tag} map%": a_win, f"{a_tag} ATK": a_atk, f"{a_tag} DEF": a_def,
                f"{b_tag} map%": b_win, f"{b_tag} ATK": b_atk, f"{b_tag} DEF": b_def,
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True)


def render_player_vs_player(conn, team_a, team_b, window, stage, five_only):
    """Align the two rosters by inferred role and compare player against player.

    Each player's role is inferred from their agent usage (see valtrack.agents),
    then the two teams are paired within each role. The headline figures, rating,
    ACS, K/D, KAST, and opening-duel win rate, are shown mirrored so like lines up
    against like. Built on the same windowed per-map detail as the per-team
    sections, so it only has figures where detail has been harvested. When the
    current-five filter is on, each roster is narrowed to its five.
    """
    st.divider()
    st.header("Player versus player")
    k = _db_key()
    a_names = current_five_set(conn, team_a) if five_only else None
    b_names = current_five_set(conn, team_b) if five_only else None
    a_players = stats.player_aggregates(
        stats.keep_players(
            cq_player_stats(k, conn, team_a["id"], window, stage), a_names
        ),
        team_a["name"],
    )
    b_players = stats.player_aggregates(
        stats.keep_players(
            cq_player_stats(k, conn, team_b["id"], window, stage), b_names
        ),
        team_b["name"],
    )
    pairs = stats.align_rosters(a_players, b_players)
    if not pairs:
        st.caption(
            "No per-map detail stored in this range for either team. Run the "
            "detail harvest (python harvest.py --pass details) to populate it."
        )
        return
    st.caption(f"{team_a['name']} (left) versus {team_b['name']} (right)")
    rows = []
    for pair in pairs:
        a = _pvp_side(pair["a"])
        b = _pvp_side(pair["b"])
        rows.append({
            "Role": pair["role"].capitalize(),
            f"{team_a['tag'] or 'A'} player": a["name"],
            "Rating ": a["rating"],
            "ACS ": a["acs"],
            "K/D ": a["kd"],
            "KAST ": a["kast"],
            "Open% ": a["open"],
            " Open%": b["open"],
            " KAST": b["kast"],
            " K/D": b["kd"],
            " ACS": b["acs"],
            " Rating": b["rating"],
            f"{team_b['tag'] or 'B'} player": b["name"],
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True)
    st.caption(
        "Roles are inferred from each player's agent usage, not an explicit "
        "source field, so they are best-effort. Players are paired position by "
        f"position within a role; an empty cell means one team had fewer players "
        f"in that role in this range. {FLAG} marks a player with fewer than "
        f"{MIN_PLAYER_MAPS} maps. Opening-duel win rate is first kills over "
        "opening duels. Figures rest on the windowed per-map detail, so a small "
        "sample shows in the per-team player tables above."
    )


def _lineup_overlap(lineup, five_names):
    """How many of a fielded lineup are on the current five, as 'k of 5'.

    Reading the lineup from who actually played sidesteps the empty roster-change
    table and is the concrete version of the roster-and-patch discounting the
    charter asks for: it shows how much of a past result the current roster
    actually owns. Returns None when no lineup is stored for that meeting.
    """
    if not lineup:
        return None
    on_five = sum(1 for name in lineup if (name or "").casefold() in five_names)
    return f"{on_five} of {len(five_names)} current starters played"


def render_head_to_head(conn, team_a, team_b, window, events, stage):
    """The two teams' direct record, with each meeting annotated for context.

    Raw head-to-head can mislead: a 2-0 record from two years ago under different
    rosters is not the same as a recent one. So each meeting is annotated with when
    it happened, LAN versus online, the maps and per-map scores, the lineup each
    side actually fielded, and how much of that lineup is on the current five. An
    older meeting the detail harvest has not reached honestly shows only its series
    score until detail fills.
    """
    st.divider()
    st.header("Head-to-head")
    k = _db_key()
    h2h = cq_h2h(k, conn, team_a["id"], team_b["id"], window, events, stage)
    if not h2h["decided"]:
        st.caption(
            "These two teams have not played a decided match in this range and "
            "event type. Teams in different regions often meet only at "
            "international events, so try widening the date range."
        )
        return
    left, right = st.columns(2)
    left.metric(f"{team_a['name']} wins", h2h["a_wins"])
    right.metric(f"{team_b['name']} wins", h2h["b_wins"])
    flag = flag_if_small(h2h["decided"], MIN_MATCHES)
    st.caption(f"{h2h['decided']} meetings{flag}")

    # How old the head-to-head is, and how it spreads across maps. A 3-1 reads
    # very differently if it is all eighteen months old or concentrated on one
    # map (item: head-to-head broken to map level and aged).
    last_meet = h2h["meetings"][0]["date"]
    age_days = (dt.date.today() - dt.date.fromisoformat(last_meet)).days
    age_note = (f"Most recent meeting {last_meet} ({age_days} days ago). "
                if last_meet else "")
    if age_days > 365:
        age_note += "Over a year old, so rosters and the meta have likely moved. "
    a_tag, b_tag = team_a["tag"] or "A", team_b["tag"] or "B"
    h2h_maps = cq_h2h_maps(
        k, conn, team_a["id"], team_b["id"], window, events, stage)
    if h2h_maps:
        map_table = [{
            "Map": r["map_name"],
            "Played": r["played"],
            f"{a_tag} won": r["a_wins"],
            f"{b_tag} won": r["b_wins"],
            "Last played": r["last_date"],
        } for r in h2h_maps]
        st.caption(age_note + "Maps the two teams played against each other:")
        st.dataframe(pd.DataFrame(map_table), hide_index=True)
        st.caption(
            "Per-map win split across the meetings, newest-sampled maps first. "
            "Side win rates are not shown for the head-to-head: over a handful of "
            "shared maps a per-side rate is noise. Concentrated on one map means "
            "the tally leans on a single map the teams keep returning to."
        )
    elif age_note:
        st.caption(age_note + "No per-map detail is stored for these meetings yet.")

    five_a = current_five_set(conn, team_a)
    five_b = current_five_set(conn, team_b)
    for m in h2h["meetings"]:
        winner = team_a["name"] if m["winner"] == "a" else team_b["name"]
        env = "LAN" if is_lan_event(m["event"]) else "online/unknown"
        fmt = stats.infer_match_format(m["a_score"], m["b_score"])
        fmt_label = f"  |  {fmt}" if fmt else ""
        header = (
            f"{m['date']}  |  {m['a_score']}-{m['b_score']}  |  {winner} won  "
            f"|  {env}{fmt_label}"
        )
        with st.expander(header):
            if m["event"]:
                st.caption(m["event"])
            maps = cq_meeting_maps(k, conn, m["match_id"])
            if maps:
                map_rows = []
                for mp in maps:
                    if not mp["map_name"]:
                        continue
                    map_rows.append({
                        "Map": mp["map_name"],
                        "Score": f"{mp['team1_score']}-{mp['team2_score']}",
                        "Winner": mp["winner_name"] or "",
                    })
                if map_rows:
                    st.dataframe(pd.DataFrame(map_rows), hide_index=True)
            else:
                st.caption(
                    "No per-map detail stored for this meeting yet, so only the "
                    "series score is known. It fills in as the detail harvest runs."
                )
            lineup_a = cq_meeting_lineup(k, conn, m["match_id"], team_a["name"])
            lineup_b = cq_meeting_lineup(k, conn, m["match_id"], team_b["name"])
            if lineup_a or lineup_b:
                c1, c2 = st.columns(2)
                with c1:
                    st.caption(f"{team_a['name']} fielded")
                    st.write(", ".join(lineup_a) if lineup_a else "not stored")
                    ov = _lineup_overlap(lineup_a, five_a)
                    if ov:
                        st.caption(ov)
                with c2:
                    st.caption(f"{team_b['name']} fielded")
                    st.write(", ".join(lineup_b) if lineup_b else "not stored")
                    ov = _lineup_overlap(lineup_b, five_b)
                    if ov:
                        st.caption(ov)
    st.caption(
        "Meetings newest first, scores from "
        f"{team_a['name']}'s point of view. LAN versus online is inferred from the "
        "event name. Lineups are who actually played that day, and the current-five "
        "overlap shows how much of the result the current roster owns."
    )


def render_common_opponents(conn, team_a, team_b, window, events, stage):
    """Opponents both teams have faced, with each team's record against them."""
    st.divider()
    st.header("Common opponents")
    common = cq_common(
        _db_key(), conn, team_a["id"], team_b["id"], window, events, stage
    )
    if not common:
        st.caption(
            "No opponents both teams have faced in this range and event type. "
            "This is common for teams in different regions over a short window."
        )
        return
    a_tag = team_a["tag"] or "A"
    b_tag = team_b["tag"] or "B"
    rows = []
    for c in common:
        a, b = c["a"], c["b"]
        a_decided, b_decided = a["wins"] + a["losses"], b["wins"] + b["losses"]
        rows.append({
            "Opponent": c["opponent"],
            f"{a_tag} record": f"{a['wins']}-{a['losses']}",
            f"{a_tag} win%": pct_num(a["wins"] / a_decided) if a_decided else None,
            f"{b_tag} record": f"{b['wins']}-{b['losses']}",
            f"{b_tag} win%": pct_num(b["wins"] / b_decided) if b_decided else None,
        })
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        column_config={
            f"{a_tag} win%": st.column_config.NumberColumn(format="%.0f%%"),
            f"{b_tag} win%": st.column_config.NumberColumn(format="%.0f%%"),
        },
    )
    st.caption(
        "Decided results against opponents both teams have played in the selected "
        "range and event type. Most useful across regions, where the two rarely "
        "meet but share third opponents. These are different opponents and events, "
        "so read them as context, not a head-to-head."
    )


def render_notes(conn, team_a, team_b):
    """A free-text note saved locally for this team pair."""
    st.divider()
    st.subheader("Notes")
    key = f"note_{journal.pair_key(team_a['id'], team_b['id'])}"
    if key not in st.session_state:
        st.session_state[key] = journal.get_note(conn, team_a["id"], team_b["id"])
    st.text_area(
        "Your observations on this matchup", key=key, height=120,
        help="Free text the data does not capture. Stored locally for this pair.",
    )
    if st.button("Save note"):
        journal.save_note(conn, team_a["id"], team_b["id"], st.session_state[key])
        st.success("Note saved.")
    st.caption("Stored locally for this team pair. Never scraped, never sent anywhere.")


def render_calibration(entries):
    """How well the user's past confidence tracked the outcomes (item: calibration).

    Closes the loop on the log: when the user leaned a team and later recorded who
    won, this groups the resolved calls by confidence and shows the hit rate at
    each level. It scores the user's own judgment, never a team, so it stays inside
    the charter and never declares a matchup winner. Calibration is noisy over a
    few calls, so it says so rather than reading much into a tiny sample.
    """
    cal = stats.calibration([dict(e) for e in entries])
    with st.expander("Your prediction calibration"):
        if cal["resolved"] == 0:
            st.caption(
                "No resolved calls with a lean yet. Add a lean when you log a "
                "matchup and record the outcome later, and this will show how often "
                "your high-confidence reads actually came in."
            )
            return
        st.caption(
            f"Over {cal['resolved']} resolved calls you leaned on, you were right "
            f"{cal['correct']} times "
            f"({pct(cal['rate'])} overall). By confidence level:"
        )
        rows = [{
            "Confidence": b["confidence"],
            "Resolved": b["resolved"],
            "Correct": b["correct"],
            "Hit rate": pct_num(b["rate"]),
        } for b in cal["buckets"]]
        st.dataframe(
            pd.DataFrame(rows), hide_index=True,
            column_config={"Hit rate": st.column_config.NumberColumn(
                "Hit rate", format="%.0f%%")},
        )
        if cal["resolved"] < 10:
            st.caption(
                "This rests on very few resolved calls, so read it as a start, not "
                "a verdict on your judgment. It sharpens as you log and resolve "
                "more matchups."
            )
        st.caption(
            "This scores your own calls (a lean plus a recorded outcome), not any "
            "team. Well-calibrated means your high-confidence calls land more often "
            "than your low-confidence ones."
        )


def render_matchup_log(conn, team_a, team_b):
    """Record a matchup with a pre-match note and confidence, resolve it later."""
    st.divider()
    st.header("Matchup log")
    with st.form("matchup_log_form", clear_on_submit=True):
        st.write(f"Log this matchup: {team_a['name']} vs {team_b['name']}")
        note = st.text_area("Pre-match note", key="log_note_input")
        confidence = st.select_slider(
            "Confidence",
            options=["very low", "low", "medium", "high", "very high"],
            value="medium",
        )
        # The team the user leans toward, so the call can be scored later for the
        # calibration readout. "No lean" stores no prediction.
        lean = st.radio(
            "Who do you lean toward?",
            ["a", "b", None],
            format_func=lambda s: (
                team_a["name"] if s == "a"
                else team_b["name"] if s == "b" else "No lean"),
            horizontal=True,
        )
        if st.form_submit_button("Add to log"):
            journal.add_log_entry(
                conn, team_a["id"], team_a["name"], team_b["id"], team_b["name"],
                note, confidence, predicted_side=lean,
            )
            st.success("Added to the log.")

    entries = journal.list_log_entries(conn)
    if not entries:
        st.caption("No log entries yet. Add one above to start tracking your calls.")
        return
    render_calibration(entries)
    confidence_options = ["very low", "low", "medium", "high", "very high"]
    st.subheader(f"Past entries ({len(entries)})")
    for e in entries:
        with st.container(border=True):
            created = (e["created_at"] or "")[:10]
            pred = e["predicted_side"] if "predicted_side" in e.keys() else None
            lean = ""
            if pred == "a":
                lean = f", leaned {e['team_a_name']}"
            elif pred == "b":
                lean = f", leaned {e['team_b_name']}"
            st.write(
                f"**{e['team_a_name']} vs {e['team_b_name']}** "
                f"({created}), confidence: {e['confidence']}{lean}"
            )
            if e["note"]:
                st.write(e["note"])
            if e["outcome"] or e["outcome_side"]:
                resolved = (e["resolved_at"] or "")[:10]
                winner = ""
                if e["outcome_side"] == "a":
                    winner = f"{e['team_a_name']} won. "
                elif e["outcome_side"] == "b":
                    winner = f"{e['team_b_name']} won. "
                hit = ""
                if pred in ("a", "b") and e["outcome_side"] in ("a", "b"):
                    hit = (" Your lean was right."
                           if pred == e["outcome_side"] else " Your lean missed.")
                st.caption(
                    f"Outcome: {winner}{e['outcome'] or ''} (recorded {resolved})"
                    f"{hit}")
            else:
                winner = st.radio(
                    "Who won?",
                    ["a", "b"],
                    format_func=lambda s, e=e: (
                        e["team_a_name"] if s == "a" else e["team_b_name"]),
                    horizontal=True,
                    key=f"log_side_{e['id']}",
                )
                detail = st.text_input(
                    "Detail (optional, e.g. the score)", key=f"log_outcome_{e['id']}"
                )
                if st.button("Save outcome", key=f"log_resolve_{e['id']}"):
                    journal.resolve_log_entry(
                        conn, e["id"], detail.strip(), outcome_side=winner)
                    st.rerun()

            with st.expander("Edit or delete"):
                new_note = st.text_area(
                    "Note", value=e["note"] or "", key=f"log_editnote_{e['id']}")
                idx = (confidence_options.index(e["confidence"])
                       if e["confidence"] in confidence_options else 2)
                new_conf = st.select_slider(
                    "Confidence", options=confidence_options, value=confidence_options[idx],
                    key=f"log_editconf_{e['id']}")
                col_save, col_del = st.columns(2)
                if col_save.button("Save edit", key=f"log_save_{e['id']}"):
                    journal.update_log_entry(conn, e["id"], new_note, new_conf)
                    st.rerun()
                if col_del.button("Delete entry", key=f"log_delete_{e['id']}"):
                    journal.delete_log_entry(conn, e["id"])
                    st.rerun()


def render_recent(conn, team, window, events, stage):
    st.divider()
    st.subheader("Recent matches")
    recent = cq_recent(_db_key(), conn, team["id"], window, events, stage, 10)
    if not recent:
        st.caption("No matches in this range.")
        return
    rows = []
    for m in recent:
        us, them = m["score"]
        score = f"{us}-{them}" if us is not None and them is not None else ""
        rows.append({
            "Date": m["date"],
            "Round": m["round"] or "",
            "Opponent": m["opponent"] or "",
            "Score": score,
            "Result": m["result"] or "",
        })
    df = pd.DataFrame(rows)
    pal = palette()
    styled = df.style.map(
        lambda v: f"color:{pal['good']}" if v == "W"
        else (f"color:{pal['bad']}" if v == "L" else ""),
        subset=["Result"],
    )
    st.dataframe(styled, hide_index=True)


def render_roster(conn, team):
    st.divider()
    st.subheader("Roster")
    roster = stats.classify_roster(cq_roster(_db_key(), conn, team["id"]))
    mains = roster["mains"]
    st.caption(f"Current five ({len(mains)} listed)")
    for p in mains:
        cap = " (C)" if p["is_captain"] else ""
        real = f" - {p['real_name']}" if p["real_name"] else ""
        st.write(f"{p['alias']}{cap}{real}")
    if roster["subs"]:
        st.caption("Stand-ins")
        for p in roster["subs"]:
            st.write(p["alias"])
    if roster["staff"]:
        st.caption("Staff")
        for p in roster["staff"]:
            role = f" ({p['role']})" if p["role"] else ""
            st.write(f"{p['alias']}{role}")
    st.caption(
        "Roles are best-effort: VLR's staff flag is unreliable here, so players "
        "and staff are split by reading the role text."
    )


def current_five_set(conn, team):
    """The casefolded current-five names for a team, for the player filter."""
    return stats.current_five_names(cq_roster(_db_key(), conn, team["id"]))


def render_roster_timeline(conn, team, window, stage):
    """Show when each player appeared, so roster changes over the range show.

    Derived from who actually played (the transactions endpoint is unreliable),
    so it is an appearance timeline rather than official join and leave dates.
    Players not in the current five are marked, which is the point: an all-time
    window can span several rosters, and this makes that visible.
    """
    st.divider()
    st.subheader("Roster timeline")
    rows = cq_appearances(_db_key(), conn, team["id"], window, stage)
    if not rows:
        st.caption("No per-map detail stored in this range, so no appearances.")
        return
    five = current_five_set(conn, team)
    table = []
    for r in rows:
        in_five = (r["player_name"] or "").casefold() in five
        table.append({
            "Player": r["player_name"],
            "On current five": "yes" if in_five else "no",
            "First seen": r["first_date"],
            "Last seen": r["last_date"],
            "Maps": r["maps"],
        })
    st.dataframe(pd.DataFrame(table), hide_index=True)
    st.caption(
        "Appearances from stored matches, not official transactions. A player "
        "marked not on the current five is a former player or stand-in whose "
        "games are still in an all-time window, which is why the current-five "
        "filter exists."
    )


def render_stale_flag(conn, team):
    """Flag a team that has not played in a while, so frozen figures are clear."""
    last = queries.last_match_date(conn, team["id"])
    if not last:
        return
    days = (dt.date.today() - dt.date.fromisoformat(last)).days
    if days >= STALE_DAYS:
        weeks = days // 7
        st.warning(
            f"Stale data: last played {days} days ago ({last}), about {weeks} "
            "weeks. These figures are effectively frozen."
        )


def team_headline(conn, team, window, events, stage, five_names=None):
    """The comparable headline figures for one team in a window.

    Win rate, pistol rate, and opening-duel rate come back as 0..100 numbers (or
    None when there is nothing to judge), the record as text, and a single
    round-weighted team rating. These are the figures the at-a-glance strip, the
    aligned core table, and the recent-versus-window block all read, so they are
    computed in one place. They are shown beside the opponent's with the gap; none
    of them is a composite or a winner call.
    """
    k = _db_key()
    rec = cq_record(k, conn, team["id"], window, events, stage)
    win = 100 * rec["wins"] / rec["decided"] if rec["decided"] else None
    p = stats.pistol_winrate(
        cq_rounds(k, conn, team["id"], window, stage), team["name"])
    o = stats.opening_duels(
        stats.keep_players(
            cq_player_opening(k, conn, team["id"], window, stage), five_names),
        team["name"],
    )
    players = stats.player_aggregates(
        stats.keep_players(
            cq_player_stats(k, conn, team["id"], window, stage), five_names),
        team["name"],
    )
    return {
        "record": f"{rec['wins']}-{rec['losses']}",
        "decided": rec["decided"],
        "win": win,
        # The won counts ride along so the gap view can build each rate's Wilson
        # band and say when two teams' bands overlap (the gap is within noise).
        "win_won": rec["wins"],
        "pistol": pct_num(p["winrate"]),
        "pistol_n": p["total"],
        "pistol_won": p["won"],
        "opening": pct_num(o["winrate"]),
        "opening_n": o["duels"],
        "opening_won": o["fk"],
        "rating": stats.team_rating(players),
    }


def render_comparison_strip(conn, team_a, team_b, window, events, stage, five_only):
    """A compact aligned row of the headline numbers with the gap (item 2).

    Before the detailed sections, this assembles the figures the user would
    otherwise have to scroll both columns to collect: win rate, pistol rate,
    opening-duel rate, and a single team rating, each shown for both teams with the
    A minus B gap. It is a per-statistic difference, never a tally of who leads.
    """
    five_a = current_five_set(conn, team_a) if five_only else None
    five_b = current_five_set(conn, team_b) if five_only else None
    a = team_headline(conn, team_a, window, events, stage, five_a)
    b = team_headline(conn, team_b, window, events, stage, five_b)
    a_tag = team_a["tag"] or "A"
    b_tag = team_b["tag"] or "B"
    rows = [
        {"Metric": "Win %", a_tag: pct100(a["win"]), b_tag: pct100(b["win"]),
         "Gap (A-B)": gap_str(a["win"], b["win"], "%")},
        {"Metric": "Pistol %", a_tag: pct100(a["pistol"]),
         b_tag: pct100(b["pistol"]),
         "Gap (A-B)": gap_str(a["pistol"], b["pistol"], "%")},
        {"Metric": "Opening-duel %", a_tag: pct100(a["opening"]),
         b_tag: pct100(b["opening"]),
         "Gap (A-B)": gap_str(a["opening"], b["opening"], "%")},
        {"Metric": "Team rating", a_tag: num2(a["rating"]),
         b_tag: num2(b["rating"]),
         "Gap (A-B)": gap_str(a["rating"], b["rating"], "", 2)},
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True)
    st.caption(
        f"{team_a['name']}: {a['record']} ({a['decided']} decided)  |  "
        f"{team_b['name']}: {b['record']} ({b['decided']} decided). "
        "Pistol, opening, and rating need per-match detail, so they cover only "
        "detailed matches. The gap is a per-statistic difference, not a score."
    )


# The headline figures the league-reference baseline positions each team against.
# Each is (label, headline key, suffix, decimals).
_REFERENCE_METRICS = [
    ("Win %", "win", "%", 0),
    ("Pistol %", "pistol", "%", 0),
    ("Opening-duel %", "opening", "%", 0),
    ("Team rating", "rating", "", 2),
]


def render_league_reference(conn, teams, team_a, team_b, window, events, stage):
    """Where each team sits against the whole franchise field on a statistic.

    A 52% pistol rate means nothing until you know the field sits near 50, so the
    A-versus-B gap is only half the read; the other half is where each team sits
    against everyone. For each headline figure this shows both teams' value, their
    percentile among the field, and the field's low, median, and high, computed
    over the same window, event, and stage filters so the baseline matches the
    numbers rather than misleading against an all-time field.

    This positions one statistic at a time, exactly as the per-row gap does. It
    never rolls the per-stat positions up into an overall standing or a
    leaderboard, which would be the ranking the charter forbids. It reads every
    team's figures, so it is behind a toggle and off by default.
    """
    with st.expander("League reference points (is this number any good?)"):
        st.caption(
            "Positions each team against the franchise field, one statistic at a "
            "time. It is not a ranking or a leaderboard: there is no overall "
            "standing, only where each team sits on each separate figure."
        )
        if not st.checkbox(
            "Compute the field baseline (reads every team for this window)",
            key="leagueref",
            help="Off by default: it computes all franchise teams' figures for the "
                 "selected window, event, and stage.",
        ):
            return
        a_tag, b_tag = team_a["tag"] or "A", team_b["tag"] or "B"
        with st.spinner("Computing the field baseline..."):
            field = {key: [] for _, key, _, _ in _REFERENCE_METRICS}
            heads = {}
            for t in teams:
                h = team_headline(conn, t, window, events, stage)
                heads[t["id"]] = h
                for _, key, _, _ in _REFERENCE_METRICS:
                    field[key].append(h[key])
        a_head = heads[team_a["id"]]
        b_head = heads[team_b["id"]]
        rows = []
        for label, key, suffix, dec in _REFERENCE_METRICS:
            summ = stats.field_summary(field[key])
            fmt = (lambda v: pct100(v)) if dec == 0 else (lambda v: num2(v))
            a_pct = stats.percentile(a_head[key], field[key])
            b_pct = stats.percentile(b_head[key], field[key])
            rows.append({
                "Metric": label,
                a_tag: fmt(a_head[key]),
                f"{a_tag} pct": None if a_pct is None else round(a_pct),
                b_tag: fmt(b_head[key]),
                f"{b_tag} pct": None if b_pct is None else round(b_pct),
                "Field low": fmt(summ["min"]),
                "Field median": fmt(summ["median"]),
                "Field high": fmt(summ["max"]),
            })
        st.dataframe(
            pd.DataFrame(rows), hide_index=True,
            column_config={
                f"{a_tag} pct": st.column_config.NumberColumn(
                    f"{a_tag} pct", format="%d", help="Percentile among the field"),
                f"{b_tag} pct": st.column_config.NumberColumn(
                    f"{b_tag} pct", format="%d", help="Percentile among the field"),
            },
        )
        st.caption(
            f"Percentile is among the {len(teams)} franchise teams over the same "
            "window, event, and stage, so the baseline matches the figures above. "
            "A team with no data in range sits out that metric's field. Each row "
            "stands alone; the positions are never summed into a ranking."
        )


def render_window_summary(conn, team, window, events, stage):
    """One line on how much data backs this column (items 14 and 19).

    States the decided and total matches and the date span in range up front, then
    how many of those matches carry per-match detail, so the user knows how
    complete a detail-derived figure is before reading it.
    """
    k = _db_key()
    s = cq_window_summary(k, conn, team["id"], window, events, stage)
    cov = cq_coverage(k, conn, team["id"], window, events, stage)
    if s["total"]:
        span = ""
        if s["min_date"] and s["max_date"]:
            span = f", {s['min_date']} to {s['max_date']} in range"
        st.caption(
            f"{s['decided']} decided of {s['total']} matches{span}. Per-map detail "
            f"available for {cov['detailed']} of {cov['total']} matches in range."
        )
    else:
        st.caption("No matches in this range and event type.")


def render_rest_load(conn, team):
    """Days of rest and recent match load, the team's competitive condition.

    Below the six-week stale threshold there is still a real difference between a
    team coming off rest with prep time and one that just played a packed weekend,
    and that matters for reading an actual upcoming match. This is plain date
    arithmetic over all stored matches (independent of the comparison window), so
    it belongs to the context cluster, not the performance figures, and it says
    nothing about why a team is rested or busy.
    """
    rl = cq_rest_load(_db_key(), conn, team["id"])
    if rl["days_since"] is None:
        return
    st.caption(
        f"Rest and load: {rl['days_since']} days since the last match "
        f"({rl['last_date']}). Last 14 days: {rl['matches_14']} matches, "
        f"{rl['maps_14']} maps. Last 30 days: {rl['matches_30']} matches, "
        f"{rl['maps_30']} maps. Competitive condition, not a performance stat."
    )


def render_lineup_continuity(conn, team, window, stage):
    """How much of the windowed sample the current five actually played.

    The current-five toggle narrows the player figures, but the team and round
    figures (side win rates, pistol, map win rate) cannot be reassigned to a
    roster, so this says how far to trust them as a read on the lineup that will
    play: the count and share of maps in the window fielded entirely by the current
    five. Reuses the same appearance-derived current-five set as the roster toggle.
    """
    five = current_five_set(conn, team)
    rows = cq_player_stats(_db_key(), conn, team["id"], window, stage)
    cont = stats.lineup_continuity(rows, five)
    if cont is None or cont["maps_total"] == 0:
        return
    pct = pct_num(cont["pct"])
    pct_text = f" ({pct:.0f}%)" if pct is not None else ""
    st.caption(
        f"Lineup continuity: the current five played {cont['maps_current']} of "
        f"{cont['maps_total']} maps in range{pct_text}. The team, map, and round "
        "figures cover everyone who played, so this is how much of them the "
        "current roster owns."
    )


def render_recent_vs_window(conn, team, window, events, stage, five_names=None):
    """Key stats over the last 90 days beside the selected window (item 8).

    A team trending hard becomes a number rather than a sparkline wiggle: each
    headline figure is shown for a rolling recent window and for the selected
    window, with the gap. An all-time-versus-recent difference per stat is a
    difference, not a composite.
    """
    st.divider()
    st.subheader("Recent form versus the selected window")
    recent_window = DateWindow(dt.date.today() - dt.timedelta(days=90),
                               dt.date.today())
    recent = team_headline(conn, team, recent_window, events, stage, five_names)
    base = team_headline(conn, team, window, events, stage, five_names)
    label = "Selected window" if not window.is_all_time else "All time"
    rows = [
        {"Metric": "Win %", "Last 90 days": pct100(recent["win"]),
         label: pct100(base["win"]),
         "Gap": gap_str(recent["win"], base["win"], "%")},
        {"Metric": "Pistol %", "Last 90 days": pct100(recent["pistol"]),
         label: pct100(base["pistol"]),
         "Gap": gap_str(recent["pistol"], base["pistol"], "%")},
        {"Metric": "Opening-duel %", "Last 90 days": pct100(recent["opening"]),
         label: pct100(base["opening"]),
         "Gap": gap_str(recent["opening"], base["opening"], "%")},
        {"Metric": "Team rating", "Last 90 days": num2(recent["rating"]),
         label: num2(base["rating"]),
         "Gap": gap_str(recent["rating"], base["rating"], "", 2)},
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True)
    st.caption(
        "Last 90 days is a rolling window from today, independent of the date "
        "range above. The gap is recent minus the selected window."
    )


def render_pressure(conn, team, window, stage):
    """Decider, distance, and comeback figures under series pressure (item 9).

    How a team does when a series is on the line: its win rate on deciding maps,
    its series win rate when a match reaches a decider, and how often it comes back
    from dropping the opening map. These are shown as separate figures, never
    folded into a single clutch or resilience rating, which would be the composite
    the charter forbids.
    """
    st.divider()
    st.subheader("Series pressure")
    k = _db_key()
    rows = cq_series(k, conn, team["id"], window, stage)
    ps = stats.pressure_stats(rows, team["name"])
    mp = stats.margin_profile(cq_map_results(k, conn, team["id"], window, stage),
                              team["name"])
    if (ps["decider_played"] == 0 and ps["comeback_chances"] == 0
            and mp["maps"] == 0):
        st.caption(
            "No multi-map series with a decider or an opening-map loss in this "
            "range yet, and no decided maps for the margin profile. These fill in "
            "as the detail harvest runs."
        )
        return
    if ps["decider_played"] or ps["comeback_chances"]:
        c1, c2, c3 = st.columns(3)
        c1.metric(
            "Decider map win%", pct(ps["decider_winrate"]),
            help=f"{ps['decider_won']} of {ps['decider_played']} deciding maps",
        )
        c2.metric(
            "Series win% in deciders", pct(ps["distance_winrate"]),
            help=f"{ps['distance_series_won']} of {ps['distance_played']} series "
                 "that reached a decider",
        )
        c3.metric(
            "Comebacks", f"{ps['comeback_won']} of {ps['comeback_chances']}",
            help="Series won after losing the opening map, over series where the "
                 "opening map was lost",
        )
        flag = flag_if_small(ps["decider_played"], MIN_MATCHES)
        st.caption(
            f"Decider is the final map of a series entered level on maps{flag}. The "
            "decider map result and the series-in-decider result are closely "
            "related but shown separately, never combined into one rating. "
            "Comebacks count dropping the opening map and still winning the series."
        )

    # Close-game and round-margin profile (item: margin). Two teams with the same
    # map win rate can be opposite in character, one winning 13-5 and losing close,
    # the other grinding everything out. These are raw distribution splits, never a
    # clutch or resilience rating.
    if mp["maps"]:
        st.markdown("**Round-margin profile**")
        m1, m2, m3 = st.columns(3)
        m1.metric(
            "Close maps (<=2 rds)", pct(mp["close_winrate"]),
            help=f"Record {mp['close_won']}-{mp['close_lost']} in maps decided by "
                 f"two rounds or fewer ({mp['close_played']} maps).")
        ot_text = (f"{mp['ot_won']} of {mp['ot_played']}"
                   if mp["ot_played"] else "none")
        m2.metric("Overtime maps", ot_text,
                  help="Maps that reached overtime, and how many were won.")
        avg_w = mp["avg_win_margin"]
        avg_l = mp["avg_loss_margin"]
        margins = (f"+{avg_w:.1f}" if avg_w is not None else "-") + " / " + (
            f"-{avg_l:.1f}" if avg_l is not None else "-")
        m3.metric("Avg win / loss margin", margins,
                  help="Average round margin in maps won and in maps lost.")
        st.caption(
            f"Over {mp['maps']} decided maps in range. A close map is decided by "
            "two rounds or fewer; an overtime map is one where the loser finished "
            "on 12 or more. These are distribution splits showing how a team wins "
            "and loses, not a clutch rating."
        )


def _player_table_rows(players):
    """Shape a player_aggregates list into the per-player table rows."""
    table = []
    for p in players:
        table.append({
            "Player": p["player_name"] + flag_if_small(p["maps"], MIN_PLAYER_MAPS),
            "Rating": p["rating"], "ACS": p["acs"], "K/D": p["kd"],
            "KAST": p["kast"], "ADR": p["adr"], "KPR": p["kpr"], "APR": p["apr"],
            "HS%": p["hs_pct"], "FKPR": p["fk_per_round"],
            "FDPR": p["fd_per_round"], "Maps": p["maps"], "Rounds": p["rounds"],
        })
    return table


def render_player_map_performance(conn, team, window, stage, five_names=None):
    """Per-player statistics split by map, not just the all-map average (item 7).

    A duelist who pops off on Ascent but goes quiet on Lotus shows two different
    lines here, which is the sharpest axis in the game. The same round-weighted
    aggregation is reused per map, so the only new thing is the split. Per-map
    samples are thin, so the small-sample flag matters more here, not less.
    """
    st.divider()
    st.subheader("Player performance by map")
    rows = stats.keep_players(
        cq_player_stats(_db_key(), conn, team["id"], window, stage), five_names
    )
    by_map = stats.player_map_aggregates(rows, team["name"])
    if not by_map:
        st.caption(DETAIL_EMPTY)
        return
    maps = sorted(by_map, key=lambda m: (-len(by_map[m]), m))
    chosen = st.selectbox(
        "Map", maps, key=f"pmp_map_{team['id']}",
        help="Player lines for the chosen map only, not the all-map average.",
    )
    st.dataframe(
        pd.DataFrame(_player_table_rows(by_map[chosen])),
        hide_index=True,
        column_config=PLAYER_COLUMN_CONFIG,
    )
    st.caption(
        f"Per-player figures on {chosen} only. Samples per player per map are "
        f"thin, so {FLAG} (fewer than {MIN_PLAYER_MAPS} maps) shows up more often "
        "here; read those lines with care."
    )


def render_overlap(conn, team_a, team_b, window, stage, pool):
    """Where the two teams' per-map strengths collide or diverge (item 11).

    A strategic framing over numbers already computed: each team's per-map win
    rate, with the map marked shared strength, shared weakness, or split. It stays
    strictly descriptive and never ranks the maps into a veto verdict, which would
    be the call the charter forbids.
    """
    st.subheader("Map-pool overlap")
    a_splits = _team_map_splits(conn, team_a, window, stage)
    b_splits = _team_map_splits(conn, team_b, window, stage)
    overlap = stats.map_pool_overlap(a_splits, b_splits, pool)
    a_tag = team_a["tag"] or "A"
    b_tag = team_b["tag"] or "B"
    rows = []
    for row in overlap:
        rows.append({
            "Map": row["map"],
            f"{a_tag} map%": pct_num(row["a_winrate"]),
            f"{b_tag} map%": pct_num(row["b_winrate"]),
            "Overlap": row["label"],
        })
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        column_config={
            f"{a_tag} map%": st.column_config.NumberColumn(format="%.0f%%"),
            f"{b_tag} map%": st.column_config.NumberColumn(format="%.0f%%"),
        },
    )
    st.caption(
        "Each team's win rate on the likely-played maps, with the map marked: "
        "shared strength (both at or above 50%), shared weakness (both below), "
        "split (one of each), or insufficient (a team has no decided map there). "
        "This is descriptive only; it does not call who wins the veto."
    )


def render_economy(conn, team, window, stage):
    """Win rate by buy type, the eco and anti-eco conversion block (item 1).

    Reads the stored per-map buy-type economy (the patched scrape gives each map
    its own econ table). Shows an honest unavailable state when no detail with
    economy is stored for the team in range. Reported per buy type, never one
    economy rating.
    """
    st.divider()
    st.subheader("Economy conversion")
    eco = stats.economy_conversion(
        cq_economy(_db_key(), conn, team["id"], window, stage), team["name"]
    )
    if not eco:
        st.caption(DETAIL_EMPTY)
        return
    labels = {"eco": "Eco (full save)", "light": "Light buy ($)",
              "half": "Half buy ($$)", "full": "Full buy ($$$)"}
    table = []
    for bt in ("eco", "light", "half", "full"):
        if bt not in eco:
            continue
        e = eco[bt]
        flag = flag_if_small(e["total"], MIN_MAP_ROUNDS)
        table.append({"Buy type": labels[bt] + flag, "Win%": pct_num(e["winrate"]),
                      "Won": e["won"], "Rounds": e["total"]})
    st.dataframe(
        pd.DataFrame(table), hide_index=True,
        column_config={"Win%": st.column_config.NumberColumn(format="%.0f%%")},
    )
    st.caption(
        "Round win rate by buy type, over the window. The eco row is the eco "
        "conversion (rounds won when the team could not fully buy). Pistols are in "
        f"the pistol section, not here; {FLAG} marks fewer than {MIN_MAP_ROUNDS} "
        "rounds. Per buy type, never folded into one economy rating."
    )


def render_clutch(conn, team, window, stage, five_names=None):
    """Team and per-player clutch (1vX) wins, by depth (item 2).

    Reads the series-level performance counts. VLR reports clutches won by
    situation, not attempts, so this shows counts and the 1v1..1v5 spread and
    never a clutch win rate. An honest unavailable state when no performance
    detail is stored. Narrowed to the current five when five_names is set.
    """
    st.divider()
    st.subheader("Clutches (1vX)")
    rows = stats.keep_players(
        cq_performance(_db_key(), conn, team["id"], window, stage), five_names
    )
    c = stats.clutch_stats(rows, team["name"])
    if c["won"] == 0:
        st.caption(DETAIL_EMPTY)
        return
    st.metric("Clutches won", c["won"],
              help="1vX rounds the team closed out, all depths, over the window")
    table = [{
        "Player": p["player_name"], "Clutches won": p["won"],
        "1v1": p["by_depth"][1], "1v2": p["by_depth"][2], "1v3": p["by_depth"][3],
        "1v4": p["by_depth"][4], "1v5": p["by_depth"][5],
    } for p in c["players"]]
    st.dataframe(pd.DataFrame(table), hide_index=True)
    st.caption(
        "Clutches won by 1vX depth, per player, over the window. VLR reports wins "
        "by situation only, not attempts, so this is counts and the depth spread, "
        "never a clutch win rate. Series totals (VLR gives these per match, not "
        "per map)."
    )


def render_multikills(conn, team, window, stage, five_names=None):
    """Per-player multikill counts (2K..5K) over the window.

    Separates a player who wins rounds in bursts from one whose fragging is spread
    thin. Counts only, never a rating. Narrowed to the current five when set.
    """
    st.divider()
    st.subheader("Multikills")
    rows = stats.keep_players(
        cq_performance(_db_key(), conn, team["id"], window, stage), five_names
    )
    mk = stats.multikill_stats(rows, team["name"])
    if not mk or all(p["total"] == 0 for p in mk):
        st.caption(DETAIL_EMPTY)
        return
    table = [{
        "Player": p["player_name"], "2K": p["k2"], "3K": p["k3"],
        "4K": p["k4"], "5K": p["k5"], "Total": p["total"],
    } for p in mk]
    st.dataframe(pd.DataFrame(table), hide_index=True)
    st.caption(
        "Rounds with N kills, per player, over the window, rarer kills first. "
        "Series totals (VLR exposes multikills per match, not per map). Counts, "
        "not a rating."
    )


def render_utility(conn, team, window, stage, five_names=None):
    """Per-player plant and defuse counts over the window.

    A hint at post-plant and retake roles. Counts only, never a rating. Narrowed
    to the current five when five_names is set.
    """
    st.divider()
    st.subheader("Plants and defuses")
    rows = stats.keep_players(
        cq_performance(_db_key(), conn, team["id"], window, stage), five_names
    )
    u = stats.utility_stats(rows, team["name"])
    if u["plants"] == 0 and u["defuses"] == 0:
        st.caption(DETAIL_EMPTY)
        return
    left, right = st.columns(2)
    left.metric("Plants", u["plants"])
    right.metric("Defuses", u["defuses"])
    table = [{"Player": p["player_name"], "Plants": p["plants"],
              "Defuses": p["defuses"]} for p in u["players"]]
    st.dataframe(pd.DataFrame(table), hide_index=True)
    st.caption(
        "Spike plants and defuses, per player, over the window (series totals). "
        "A hint at post-plant and retake tendencies, never a quality rating."
    )


def render_win_conditions(conn, team, window, stage):
    """How a team's round wins are achieved, by condition and side.

    Splits round wins into elimination, defuse, time, and spike, and by attack and
    defense, so the user can read playstyle the side splits do not give (a defense
    that wins by time or defuse plays differently from one that wins by
    elimination). Descriptive counts, never a quality score. Needs detail
    harvested with the patched round scraper, else an honest unavailable state.
    """
    st.divider()
    st.subheader("Round win conditions")
    wc = stats.round_win_conditions(
        cq_win_types(_db_key(), conn, team["id"], window, stage))
    if wc["total"] == 0:
        st.caption(DETAIL_EMPTY)
        return
    labels = {"elim": "Elimination", "defuse": "Defuse", "time": "Time",
              "boom": "Spike"}
    rows = []
    for t in ("elim", "defuse", "time", "boom"):
        share = wc["by_type"][t] / wc["total"] if wc["total"] else None
        rows.append({
            "Condition": labels[t],
            "ATK": wc["by_side"]["atk"][t],
            "DEF": wc["by_side"]["def"][t],
            "Total": wc["by_type"][t],
            "Share%": pct_num(share),
        })
    st.dataframe(
        pd.DataFrame(rows), hide_index=True,
        column_config={"Share%": st.column_config.NumberColumn(format="%.0f%%")},
    )
    st.caption(
        f"How {team['name']}'s {wc['total']} round wins with a known condition "
        "were achieved, split by attack and defense side. Spike and most "
        "eliminations come on attack; defuse and time are defense holds. Counts, "
        "not a score."
    )


def render_compositions(conn, team, window, stage):
    """The agent compositions a team runs per map, with the record on each (item 3).

    Folds the per-player agents back into the five-agent comp the team fielded on
    each map. Descriptive: it shows what a team brings and how it fared, never a
    pick recommendation.
    """
    st.divider()
    st.subheader("Agent compositions per map")
    by_map = stats.map_compositions(
        cq_compositions(_db_key(), conn, team["id"], window, stage), team["name"]
    )
    if not by_map:
        st.caption(DETAIL_EMPTY)
        return
    maps = sorted(by_map, key=lambda m: (-sum(c["played"] for c in by_map[m]), m))
    chosen = st.selectbox(
        "Map", maps, key=f"comp_map_{team['id']}",
        help="The compositions this team ran on the chosen map.",
    )
    table = [{
        "Composition": ", ".join(c["agents"]) or "(unknown)",
        "Played": c["played"], "Won": c["won"], "Win%": pct_num(c["winrate"]),
    } for c in by_map[chosen]]
    st.dataframe(
        pd.DataFrame(table), hide_index=True,
        column_config={"Win%": st.column_config.NumberColumn(format="%.0f%%")},
    )
    st.caption(
        f"The five-agent comps {team['name']} ran on {chosen}, most played first, "
        "with the record on each. Win rate is over times the comp was played. "
        "Descriptive only, not a comp recommendation."
    )


def _map_opp_rank_note(conn, team, window, stage):
    """A short caption on the average opponent rank behind a team's maps (item 5)."""
    rows = cq_map_opp_rank(_db_key(), conn, team["id"], window, stage)
    ranked = [r for r in rows if r["avg_rank"] is not None]
    if not ranked:
        return None
    overall = sum(r["avg_rank"] * r["ranked"] for r in ranked)
    denom = sum(r["ranked"] for r in ranked)
    if not denom:
        return None
    return (
        f"Opponent quality: the maps above were played against an average "
        f"opponent regional rank of about #{overall / denom:.0f}. Ranks are VLR's "
        "current snapshot, so this is a rough signal."
    )


def render_aligned(conn, team_a, team_b, window, events, stage, five_only,
                   current_pool=None, pool_only=False):
    """One shared table per core stat with the gap, instead of two columns.

    This is the charter line the side-by-side layout under-delivers: each row shows
    A, B, and the gap, with maps in a single shared order so they line up (items 1
    and 5). It is a per-statistic difference throughout, never a cross-category
    tally or an overall rating.
    """
    five_a = current_five_set(conn, team_a) if five_only else None
    five_b = current_five_set(conn, team_b) if five_only else None
    a = team_headline(conn, team_a, window, events, stage, five_a)
    b = team_headline(conn, team_b, window, events, stage, five_b)
    a_tag = team_a["tag"] or "A"
    b_tag = team_b["tag"] or "B"

    st.subheader("Core figures, aligned")
    # (label, A value, B value, suffix, decimals, sample text, A n, B n, threshold).
    # The counts and threshold drive the dimming of thin cells (item: dim the
    # unreliable cells); a zero threshold means the figure carries no sample to flag.
    core_metrics = [
        ("Win %", a["win"], b["win"], "%", 0,
         f"{a['decided']} vs {b['decided']} decided",
         a["decided"], b["decided"], MIN_MATCHES),
        ("Pistol %", a["pistol"], b["pistol"], "%", 0,
         f"{a['pistol_n']} vs {b['pistol_n']} pistols",
         a["pistol_n"], b["pistol_n"], MIN_PISTOLS),
        ("Opening-duel %", a["opening"], b["opening"], "%", 0,
         f"{a['opening_n']} vs {b['opening_n']} duels",
         a["opening_n"], b["opening_n"], MIN_DUELS),
        ("Team rating", a["rating"], b["rating"], "", 2, "round-weighted",
         None, None, 0),
    ]
    core_rows, core_meta = [], []
    for label, av, bv, suffix, dec, sample, a_n, b_n, thresh in core_metrics:
        fmt = pct100 if dec == 0 else num2
        core_rows.append({
            "Metric": label, a_tag: fmt(av), b_tag: fmt(bv),
            "Gap (A-B)": gap_str(av, bv, suffix, dec), "Sample": sample,
        })
        leader = None
        if av is not None and bv is not None and av != bv:
            leader = a_tag if av > bv else b_tag
        a_small = bool(thresh) and stats.is_small_sample(a_n, thresh)
        b_small = bool(thresh) and stats.is_small_sample(b_n, thresh)
        core_meta.append((
            leader, None if av is None or bv is None else av - bv, a_small, b_small))
    core_df = pd.DataFrame(core_rows)
    pal = palette()

    def style_core(row):
        # Per-row leader cue (item 11), gap coloring (item 12), and dimming of
        # thin cells (item: dim the unreliable). Strictly per-row, never a tally.
        leader, gap, a_small, b_small = core_meta[row.name]
        styles = {col: "" for col in row.index}
        for col in (a_tag, b_tag, "Gap (A-B)"):
            styles[col] = "text-align:right"   # align numbers (item 15)
        if leader:
            styles[leader] += f";background-color:{pal['lead']}"
        if gap is not None and gap != 0:
            styles["Gap (A-B)"] += f";color:{pal['good'] if gap > 0 else pal['bad']}"
        # A thin sample is faded and italic so the eye slides past it. The value
        # and its sample size both stay visible; only the emphasis drops.
        if a_small:
            styles[a_tag] += ";color:#9a9a9a;font-style:italic"
        if b_small:
            styles[b_tag] += ";color:#9a9a9a;font-style:italic"
        return pd.Series(styles)

    st.dataframe(core_df.style.apply(style_core, axis=1), hide_index=True)
    st.caption(
        "The leading team's cell is shaded per row and the gap is colored by sign. "
        "A cell resting on a thin sample is faded and italic, so the eye is drawn "
        "to the numbers that carry weight; the value and its sample stay visible. "
        "This marks each row's difference; it is not a tally and calls no winner."
    )

    st.subheader("Per-map and side win rates, aligned")
    a_splits = _team_map_splits(conn, team_a, window, stage)
    b_splits = _team_map_splits(conn, team_b, window, stage)
    if not a_splits and not b_splits:
        st.caption(DETAIL_EMPTY)
        return
    names = {n for n in (set(a_splits) | set(b_splits))
             if map_visible(n, current_pool, pool_only)}
    if not names:
        st.caption("No current-rotation maps with detail in this range."
                   if pool_only else DETAIL_EMPTY)
        return

    def plays(splits, name):
        m = splits.get(name)
        return (m["won"] + m["lost"]) if m else 0

    # A single shared order so the rows line up: most-played across both teams
    # first, then by name. This is item 5, folded into the aligned table.
    ordered = sorted(names, key=lambda n: (-(plays(a_splits, n)
                                            + plays(b_splits, n)), n))
    rows, small_flags = [], []
    for name in ordered:
        am = a_splits.get(name)
        bm = b_splits.get(name)
        a_win = am["map_winrate"] if am else None
        b_win = bm["map_winrate"] if bm else None
        seen = ((am["rounds_total"] if am else 0)
                + (bm["rounds_total"] if bm else 0))
        small_flags.append(stats.is_small_sample(seen, MIN_MAP_ROUNDS))
        rows.append({
            "Map": name + rotation_mark(name, current_pool)
                   + flag_if_small(seen, MIN_MAP_ROUNDS),
            f"{a_tag} map%": pct_num(a_win),
            f"{b_tag} map%": pct_num(b_win),
            "Gap (A-B)": gap_str(pct_num(a_win), pct_num(b_win), "%"),
            f"{a_tag} ATK": pct_num(am["atk_winrate"]) if am else None,
            f"{b_tag} ATK": pct_num(bm["atk_winrate"]) if bm else None,
            f"{a_tag} DEF": pct_num(am["def_winrate"]) if am else None,
            f"{b_tag} DEF": pct_num(bm["def_winrate"]) if bm else None,
        })
    def bar(label):
        return st.column_config.ProgressColumn(
            label, format="%.0f%%", min_value=0, max_value=100)

    def pctcol(label):
        return st.column_config.NumberColumn(label, format="%.0f%%")

    # Fade the text columns of a thin-sample map row so the eye slides past it. The
    # map% columns are progress bars (their fill is left intact), and the row, the
    # value, and the sample marker all stay; only the emphasis drops.
    map_df = pd.DataFrame(rows)
    dim_cols = ["Map", "Gap (A-B)", f"{a_tag} ATK", f"{b_tag} ATK",
                f"{a_tag} DEF", f"{b_tag} DEF"]

    def style_map(row):
        faded = "color:#9a9a9a;font-style:italic" if small_flags[row.name] else ""
        return pd.Series({col: (faded if col in dim_cols else "")
                          for col in row.index})

    st.dataframe(
        map_df.style.apply(style_map, axis=1),
        hide_index=True,
        column_config={
            f"{a_tag} map%": bar(f"{a_tag} map%"),
            f"{b_tag} map%": bar(f"{b_tag} map%"),
            f"{a_tag} ATK": pctcol(f"{a_tag} ATK"),
            f"{b_tag} ATK": pctcol(f"{b_tag} ATK"),
            f"{a_tag} DEF": pctcol(f"{a_tag} DEF"),
            f"{b_tag} DEF": pctcol(f"{b_tag} DEF"),
        },
    )
    st.caption(
        "Maps in a shared order so the rows line up. Map win% is over decided "
        f"maps, side rates over rounds on that side. {FLAG} marks a map with fewer "
        f"than {MIN_MAP_ROUNDS} rounds across both teams, and such rows are faded "
        "so a thin sample does not read as solidly as a deep one. The gap is A "
        "minus B in points; it is a per-row difference, not a tally."
    )


def render_glossary():
    """An inline glossary describing how VALTrack computes each statistic (item 15).

    The definitions describe VALTrack's own computation (round-weighted means, the
    winner-only side math, opening duels as per-side totals), so the explanation
    matches the number rather than a generic VLR definition that may differ.
    """
    with st.expander("Glossary: what each statistic means and how it is computed"):
        st.markdown(
            "- **ACS**: average combat score per round. Round-weighted across the "
            "player's maps (each map weighted by its rounds), the way VLR sums a "
            "season average.\n"
            "- **KAST**: percent of rounds with a kill, assist, survival, or "
            "trade. Round-weighted across maps.\n"
            "- **ADR**: average damage per round, round-weighted across maps.\n"
            "- **K/D**: total kills divided by total deaths over the range (summed "
            "then divided, not an average of per-map ratios).\n"
            "- **KPR / APR**: kills and assists per round, totals over the rounds "
            "the player was on the server.\n"
            "- **HS%**: headshot percentage. Round-weighted as an approximation, "
            "since the source stores only the per-map percentage, not raw hits.\n"
            "- **Rating range / sigma**: the spread of a player's per-map rating "
            "(low-high band, and the standard deviation across maps). High sigma "
            "means feast-or-famine; it needs at least two maps.\n"
            "- **FK / FD, FKPR / FDPR**: first kills and first deaths, and those "
            "per round.\n"
            "- **Opening duel**: the first kill or first death of a round. The win "
            "rate is first kills over opening duels (first kills plus first "
            "deaths). The attack and defense splits are per-side totals over the "
            "map, not a round-by-round timeline, since the source stores only "
            "per-map per-side first-blood counts.\n"
            "- **ATK / DEF win%**: side round win rate. The rounds table stores "
            "only each round's winner and side, but the two teams are always on "
            "opposite sides, so a team's attack rate is its rounds won attacking "
            "plus the opponent's rounds lost defending, over all attack rounds.\n"
            "- **Pistol %**: win rate of round 1 and round 13 (the pistols). "
            "Reported at team level only, since per-map there are too few.\n"
            "- **Next round after a pistol**: win rate of round 2 and round 14, "
            "split by whether the pistol was won or lost. A proxy for economy "
            "conversion from reliable round data, not true eco conversion.\n"
            "- **Map win%**: wins over decided maps. **Round margin**: a close map "
            "is decided by two rounds or fewer; an overtime map is one where the "
            "loser finished on 12 or more.\n"
            "- **Rating (team and player)**: VLR's composite per-round rating, "
            "round-weighted across maps; the team rating round-weights its "
            "players. It is a summary shown beside the opponent's, never a winner "
            "call.\n"
            "- **Decider**: the final map of a series entered level on maps, with "
            "at least one map already won by each side.\n"
            "- **Percentile (league reference)**: where a team's figure sits among "
            "the franchise field for the same window, by the midpoint convention "
            "(values below, plus half of those equal). Per statistic, never summed "
            "into a standing.\n"
            "- **Stage**: group/swiss versus playoff/elimination, classified from "
            "the bracket round label (best-effort; ambiguous labels are excluded "
            "from both)."
        )


# The optional per-team sections, in render order, that the section picker
# (item 18) can hide so the user can focus the long column on what they want.
TEAM_SECTIONS = [
    "Record and form",
    "Snapshot",
    "Map splits",
    "Compositions",
    "Pistol",
    "Economy",
    "Win conditions",
    "Opening duels",
    "Player stats",
    "Player by map",
    "Clutches",
    "Multikills",
    "Plants and defuses",
    "Series pressure",
    "Recent vs window",
    "Roster timeline",
    "Recent matches",
    "Roster",
]


def render_team(conn, column, team, window, five_only, events, stage, sections,
                highlight=None, current_pool=None, pool_only=False):
    """Render one team's comparison column, limited to the chosen sections."""
    with column:
        st.header(team["name"])
        subtitle = team["league"].capitalize()
        if team["region"]:
            subtitle += f" / {team['region'].upper()}"
        if team["tag"]:
            subtitle += f" / {team['tag']}"
        st.caption(subtitle)
        if team["logo"]:
            st.image(team["logo"], width=80)

        render_stale_flag(conn, team)
        render_window_summary(conn, team, window, events, stage)
        render_rest_load(conn, team)
        five_names = current_five_set(conn, team) if five_only else None
        render_lineup_continuity(conn, team, window, stage)

        def on(name):
            return name in sections

        if on("Record and form"):
            render_record_and_form(conn, team, window, events, stage)
        if on("Snapshot"):
            render_snapshot(team)
        if on("Map splits"):
            render_map_splits(conn, team, window, stage, highlight,
                              current_pool, pool_only)
        if on("Compositions"):
            render_compositions(conn, team, window, stage)
        if on("Pistol"):
            render_pistol(conn, team, window, stage)
        if on("Economy"):
            render_economy(conn, team, window, stage)
        if on("Win conditions"):
            render_win_conditions(conn, team, window, stage)
        if on("Opening duels"):
            render_opening(conn, team, window, stage, five_names)
        if on("Player stats"):
            render_player_stats(conn, team, window, stage, five_names)
        if on("Player by map"):
            render_player_map_performance(conn, team, window, stage, five_names)
        if on("Clutches"):
            render_clutch(conn, team, window, stage, five_names)
        if on("Multikills"):
            render_multikills(conn, team, window, stage, five_names)
        if on("Plants and defuses"):
            render_utility(conn, team, window, stage, five_names)
        if on("Series pressure"):
            render_pressure(conn, team, window, stage)
        if on("Recent vs window"):
            render_recent_vs_window(conn, team, window, events, stage, five_names)
        if on("Roster timeline"):
            render_roster_timeline(conn, team, window, stage)
        if on("Recent matches"):
            render_recent(conn, team, window, events, stage)
        if on("Roster"):
            render_roster(conn, team)


def _likely_pool(conn, team_a, team_b, window):
    """The reconstructed likely-played map pool for the two teams, or None.

    Reuses the veto aggregation so the dashboard duel board and the map highlight
    (item 20) line up with the flagship reconstruction. Returns the likely-played
    maps, or None when there is no veto data in range.
    """
    k = _db_key()
    a_tend = veto.team_tendencies(
        cq_vetos(k, conn, team_a["id"], window), team_a["tag"], team_a["name"])
    b_tend = veto.team_tendencies(
        cq_vetos(k, conn, team_b["id"], window), team_b["tag"], team_b["name"])
    pool = veto.active_pool(a_tend, b_tend)
    if not pool:
        return None
    rec = veto.reconstruct(a_tend, b_tend, pool)
    return rec["likely_played"] or pool


def render_duel_board(conn, team_a, team_b, window, stage, pool=None):
    """Each likely-played map as the cross-side duel between the teams (item 22).

    The sharpest predictive axis in the game: instead of each team's attack and
    defense in isolation, A attacking is shown next to B defending, then the
    mirror. Sorted to the likely-played pool when one is known. Per-map and
    per-side throughout, never a who-wins-the-map call.
    """
    st.subheader("Map duel board")
    a_splits = _team_map_splits(conn, team_a, window, stage)
    b_splits = _team_map_splits(conn, team_b, window, stage)
    if not a_splits and not b_splits:
        st.caption(DETAIL_EMPTY)
        return
    if pool is None:
        pool = sorted(
            set(a_splits) | set(b_splits),
            key=lambda n: -((a_splits.get(n, {}).get("rounds_total", 0))
                            + (b_splits.get(n, {}).get("rounds_total", 0))),
        )
    board = stats.map_duel_board(a_splits, b_splits, pool)
    a_tag, b_tag = team_a["tag"] or "A", team_b["tag"] or "B"
    rows = []
    for d in board:
        rounds = d["a_rounds"] + d["b_rounds"]
        rows.append({
            "Map": d["map"] + flag_if_small(rounds, MIN_MAP_ROUNDS),
            f"{a_tag} ATK": pct_num(d["a_atk"]),
            f"{b_tag} DEF": pct_num(d["b_def"]),
            f"{b_tag} ATK": pct_num(d["b_atk"]),
            f"{a_tag} DEF": pct_num(d["a_def"]),
            f"{a_tag} map%": pct_num(d["a_map"]),
            f"{b_tag} map%": pct_num(d["b_map"]),
        })

    def pctcol(label):
        return st.column_config.NumberColumn(label, format="%.0f%%")

    st.dataframe(
        pd.DataFrame(rows), hide_index=True,
        column_config={c: pctcol(c) for c in (
            f"{a_tag} ATK", f"{b_tag} DEF", f"{b_tag} ATK", f"{a_tag} DEF",
            f"{a_tag} map%", f"{b_tag} map%")},
    )

    # The cross-side duel as opposed bars, so a side mismatch is visible at a
    # glance instead of decoded from four numbers in a row. Per duel pair the two
    # bars sit adjacent (A attacking next to B defending, then the mirror), colored
    # by team with the defending side hatched. The 50% line is the only reference;
    # it deliberately does not call who wins the map.
    chart = [d for d in board
             if any(d[k] is not None
                    for k in ("a_atk", "b_def", "b_atk", "a_def"))]
    if chart:
        names = [d["map"] for d in chart]
        a_color, b_color = "#4c78a8", "#b279a2"

        def duel_y(key):
            return [100 * d[key] if d[key] is not None else None for d in chart]

        fig = go.Figure()
        fig.add_bar(name=f"{a_tag} ATK", x=names, y=duel_y("a_atk"),
                    marker_color=a_color)
        fig.add_bar(name=f"{b_tag} DEF", x=names, y=duel_y("b_def"),
                    marker_color=b_color, marker_pattern_shape="/")
        fig.add_bar(name=f"{b_tag} ATK", x=names, y=duel_y("b_atk"),
                    marker_color=b_color)
        fig.add_bar(name=f"{a_tag} DEF", x=names, y=duel_y("a_def"),
                    marker_color=a_color, marker_pattern_shape="/")
        fig.add_hline(y=50, line_dash="dot", line_color="gray", opacity=0.5)
        fig.update_layout(
            barmode="group", height=300,
            margin=dict(l=0, r=0, t=10, b=0),
            yaxis=dict(title="win %", range=[0, 100]),
            legend=dict(orientation="h", y=1.15),
        )
        st.plotly_chart(fig, width="stretch",
                        key=f"duelchart_{team_a['id']}_{team_b['id']}")

    st.caption(
        f"Each map as the side duel: {a_tag} attacking sits beside {b_tag} "
        f"defending, then {b_tag} attacking beside {a_tag} defending (the "
        "defending side is hatched). These are per-map, per-side win rates; "
        f"{FLAG} marks fewer than {MIN_MAP_ROUNDS} rounds across both teams. The "
        "50% line is the only reference; it does not call who wins the map."
    )


def render_map_breakdown(conn, team_a, team_b, window, events, stage):
    """One block per likely map, assembling the whole per-map read in one place.

    A veto is reasoned map by map, but the per-map picture is otherwise scattered
    across the map splits, the duel board, the compositions, and the head-to-head.
    This pulls them together per likely-played map: both teams' record and win
    rate, the cross-side duels, each team's sample recency, each team's most-run
    composition, and the head-to-head on that map. Descriptive per map throughout;
    it never labels a map as one team's or calls who wins it.
    """
    st.divider()
    st.header("Map-by-map breakdown")
    k = _db_key()
    a_splits = _team_map_splits(conn, team_a, window, stage)
    b_splits = _team_map_splits(conn, team_b, window, stage)
    if not a_splits and not b_splits:
        st.caption(DETAIL_EMPTY)
        return
    pool = _likely_pool(conn, team_a, team_b, window)
    if not pool:
        pool = sorted(
            set(a_splits) | set(b_splits),
            key=lambda n: -((a_splits.get(n, {}).get("rounds_total", 0))
                            + (b_splits.get(n, {}).get("rounds_total", 0))),
        )[:5]
    board = {d["map"]: d for d in stats.map_duel_board(a_splits, b_splits, pool)}
    a_comps = stats.map_compositions(
        cq_compositions(k, conn, team_a["id"], window, stage), team_a["name"])
    b_comps = stats.map_compositions(
        cq_compositions(k, conn, team_b["id"], window, stage), team_b["name"])
    a_rec = _map_recency(conn, team_a, window, stage)
    b_rec = _map_recency(conn, team_b, window, stage)
    h2h = {r["map_name"]: r for r in
           cq_h2h_maps(k, conn, team_a["id"], team_b["id"], window, events, stage)}
    a_tag, b_tag = team_a["tag"] or "A", team_b["tag"] or "B"
    st.caption(
        "Each likely-played map in one place: record and win rate, the side "
        "duels, sample freshness, the most-run composition, and the head-to-head. "
        "Descriptive per map; it does not call who wins a map."
    )

    def top_comp(comps, m):
        lst = comps.get(m)
        if not lst:
            return "not stored"
        c = lst[0]
        return f"{', '.join(c['agents'])} ({c['won']}-{c['played'] - c['won']})"

    def recency_note(rec, m):
        info = rec.get(m)
        if not info or not info["maps"]:
            return "no maps in range"
        months = _months_ago(info["last"])
        ago = f"{months}mo ago" if months is not None else "?"
        return f"{info['maps']} maps, last {info['last']} ({ago})"

    for m in pool:
        d = board.get(m)
        rounds = (d["a_rounds"] + d["b_rounds"]) if d else 0
        flag = flag_if_small(rounds, MIN_MAP_ROUNDS)
        with st.expander(f"{m}{flag}", expanded=(m == pool[0])):
            c1, c2 = st.columns(2)
            for col, team, splits, comps, rec in (
                (c1, team_a, a_splits.get(m), a_comps, a_rec),
                (c2, team_b, b_splits.get(m), b_comps, b_rec),
            ):
                with col:
                    st.markdown(f"**{team['name']}**")
                    if splits and (splits["won"] + splits["lost"]) > 0:
                        st.metric("Map record",
                                  f"{splits['won']}-{splits['lost']}",
                                  help="decided maps in range")
                        st.caption(
                            f"Map win {pct(splits['map_winrate'])} · "
                            f"ATK {pct(splits['atk_winrate'])} · "
                            f"DEF {pct(splits['def_winrate'])}")
                    else:
                        st.caption("No decided maps here in range.")
                    st.caption("Sample: " + recency_note(rec, m))
                    st.caption("Most-run comp: " + top_comp(comps, m))
            if d:
                duel = pd.DataFrame([
                    {"Side duel": f"{a_tag} attack", "Win%": pct_num(d["a_atk"]),
                     "vs": f"{b_tag} defense", "Opp win%": pct_num(d["b_def"])},
                    {"Side duel": f"{b_tag} attack", "Win%": pct_num(d["b_atk"]),
                     "vs": f"{a_tag} defense", "Opp win%": pct_num(d["a_def"])},
                ])
                st.dataframe(
                    duel, hide_index=True,
                    column_config={
                        "Win%": st.column_config.NumberColumn(format="%.0f%%"),
                        "Opp win%": st.column_config.NumberColumn(format="%.0f%%"),
                    },
                )
            hm = h2h.get(m)
            if hm and hm["played"]:
                st.caption(
                    f"Head-to-head on {m}: {team_a['name']} {hm['a_wins']} - "
                    f"{hm['b_wins']} {team_b['name']} over {hm['played']} "
                    f"(last {hm['last_date']}).")
            else:
                st.caption(f"No head-to-head on {m} in this range.")


def render_coverage_strip(conn, team_a, team_b, window, stage):
    """A compact per-pair data-coverage line for the detail-dependent sections (P1).

    The economy, clutch and other performance, and round win-condition sections
    only have data where the rich tables were harvested. This says up front, per
    section, how many maps or matches back it for each team in this window, so the
    user knows what is answerable here instead of scrolling into empty sections to
    find out. Meta-honesty about sample, never a figure or a verdict.
    """
    k = _db_key()
    a = cq_rich_coverage(k, conn, team_a["id"], window, stage)
    b = cq_rich_coverage(k, conn, team_b["id"], window, stage)
    a_tag, b_tag = team_a["tag"] or "A", team_b["tag"] or "B"
    rows = [
        {"Rich section": "Economy (maps)",
         a_tag: a["economy_maps"], b_tag: b["economy_maps"]},
        {"Rich section": "Clutches and performance (matches)",
         a_tag: a["performance_matches"], b_tag: b["performance_matches"]},
        {"Rich section": "Round win conditions (rounds)",
         a_tag: a["win_condition_rounds"], b_tag: b["win_condition_rounds"]},
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True)
    blanks = [r["Rich section"].split(" (")[0]
              for r in rows if r[a_tag] == 0 or r[b_tag] == 0]
    if blanks:
        st.caption(
            "A 0 means that section will show its empty state for at least one "
            "team in this window. Thin here: " + ", ".join(blanks) + ". Backfill "
            "the rich tables with python harvest.py --pass details --redetail "
            "--since <date> if a section you need is empty."
        )
    else:
        st.caption(
            "All detail-dependent sections (economy, clutches, win conditions) "
            "have data for both teams in this window."
        )


def render_context_panel(conn, team_a, team_b, window, events, stage, upcoming=None):
    """The data-honesty flags up front, so a misleading number is caught (item 23).

    Collects the signals already computed elsewhere (stale teams, thin detail
    coverage, an old last meeting, an empty head-to-head) into one panel, plus a
    note from the upcoming-match tag when set. Fires only when relevant. This is
    the charter's data-honesty principle made the first thing the user reads.
    """
    st.subheader("Read this carefully")
    k = _db_key()
    today = dt.date.today()
    msgs = []
    for team in (team_a, team_b):
        last = queries.last_match_date(conn, team["id"])
        if last:
            days = (today - dt.date.fromisoformat(last)).days
            if days >= STALE_DAYS:
                msgs.append(
                    f"{team['name']} last played {days} days ago ({last}), so its "
                    "figures are effectively frozen.")
        cov = cq_coverage(k, conn, team["id"], window, events, stage)
        if cov["total"] and cov["detailed"] < cov["total"]:
            msgs.append(
                f"{team['name']}: per-map detail covers {cov['detailed']} of "
                f"{cov['total']} matches in range, so the detail figures rest on a "
                "subset of its matches.")
    h2h = cq_h2h(k, conn, team_a["id"], team_b["id"], window, events, stage)
    if h2h["decided"]:
        last_meet = h2h["meetings"][0]["date"]
        days = (today - dt.date.fromisoformat(last_meet)).days
        if days > 180:
            msgs.append(
                f"The teams last met {days} days ago ({last_meet}); rosters and "
                "the meta may have moved since, so weight the head-to-head with "
                "care.")
    else:
        msgs.append(
            "These two teams have no decided meeting in this range, so the "
            "head-to-head is empty. Lean on common opponents instead.")
    if upcoming:
        if upcoming.get("is_lan"):
            msgs.append(
                "The tagged upcoming match is on LAN. If most of the data here is "
                "online, weight LAN results higher; the event-type filter can "
                "narrow to LAN.")
        if upcoming.get("match_date"):
            msgs.append(
                f"Upcoming match tagged for {upcoming['match_date']}"
                + (f" ({upcoming['event_name']})" if upcoming.get("event_name") else "")
                + ".")
    if not msgs:
        st.success(
            "No data-honesty flags fired for this window. Still read the sample "
            "sizes on each figure.")
    else:
        for m in msgs:
            st.warning(m)


def _map_side_edges(conn, team_a, team_b, window, stage, pool):
    """The biggest per-map and per-side gaps between two teams, for the gap view.

    In this game the decisive edges are usually map-specific or side-specific, not
    team-level, so the synthesis view would miss them with team rows alone. This
    builds metric rows from the duel board (each team's map win rate, and the two
    cross-side duels A-attack vs B-defense and A-defense vs B-attack) on the
    likely-played maps. Small-sample maps are skipped so a noisy three-round map
    cannot masquerade as the biggest difference. Returns rank_metric_gaps rows,
    capped to the few largest, each "a" being team A's figure for a consistent
    Leads column.
    """
    a_splits = _team_map_splits(conn, team_a, window, stage)
    b_splits = _team_map_splits(conn, team_b, window, stage)
    if not a_splits and not b_splits:
        return []
    if not pool:
        pool = sorted(set(a_splits) | set(b_splits))
    board = stats.map_duel_board(a_splits, b_splits, pool)
    candidates = []
    for d in board:
        # Only judge a map where both teams have a real sample on it, so the edge
        # is trustworthy rather than a small-sample artifact.
        if d["a_rounds"] < MIN_MAP_ROUNDS or d["b_rounds"] < MIN_MAP_ROUNDS:
            continue
        m = d["map"]
        if d["a_map"] is not None and d["b_map"] is not None:
            candidates.append({"metric": f"Map% · {m}", "a": pct_num(d["a_map"]),
                               "b": pct_num(d["b_map"]), "suffix": "%", "dec": 0})
        if d["a_atk"] is not None and d["b_def"] is not None:
            candidates.append({
                "metric": f"{team_a['tag'] or 'A'} ATK vs {team_b['tag'] or 'B'} DEF · {m}",
                "a": pct_num(d["a_atk"]), "b": pct_num(d["b_def"]),
                "suffix": "%", "dec": 0})
        if d["a_def"] is not None and d["b_atk"] is not None:
            candidates.append({
                "metric": f"{team_a['tag'] or 'A'} DEF vs {team_b['tag'] or 'B'} ATK · {m}",
                "a": pct_num(d["a_def"]), "b": pct_num(d["b_atk"]),
                "suffix": "%", "dec": 0})
    ranked = stats.rank_metric_gaps(candidates)
    return [r for r in ranked if r["abs_gap"] is not None][:6]


def render_gap_view(conn, team_a, team_b, window, events, stage, five_only,
                    pool=None):
    """Comparable headline figures sorted by the size of the gap (item 24).

    The read a predictor assembles by hand: the rows where the teams differ most
    on top, the near-ties at the bottom, each tagged with which side leads that
    one row. Team-level figures (win, pistol, opening, rating) are joined by the
    largest per-map and per-side edges, since those are usually where a matchup is
    decided. The hard line: this sorts per-statistic differences and marks the
    per-row leader. It never counts how many rows a team leads or calls a winner.
    """
    st.subheader("Biggest differences")
    five_a = current_five_set(conn, team_a) if five_only else None
    five_b = current_five_set(conn, team_b) if five_only else None
    a = team_headline(conn, team_a, window, events, stage, five_a)
    b = team_headline(conn, team_b, window, events, stage, five_b)
    a_tag, b_tag = team_a["tag"] or "A", team_b["tag"] or "B"
    # The win, pistol, and opening rates carry their won/total counts so a Wilson
    # confidence band can be built for each side and the row tagged when the two
    # bands overlap (the gap is within sampling noise, not a resolvable edge).
    metrics = [
        {"metric": "Win %", "a": a["win"], "b": b["win"], "suffix": "%", "dec": 0,
         "a_won": a["win_won"], "a_total": a["decided"],
         "b_won": b["win_won"], "b_total": b["decided"]},
        {"metric": "Pistol %", "a": a["pistol"], "b": b["pistol"], "suffix": "%",
         "dec": 0, "a_won": a["pistol_won"], "a_total": a["pistol_n"],
         "b_won": b["pistol_won"], "b_total": b["pistol_n"]},
        {"metric": "Opening-duel %", "a": a["opening"], "b": b["opening"],
         "suffix": "%", "dec": 0, "a_won": a["opening_won"], "a_total": a["opening_n"],
         "b_won": b["opening_won"], "b_total": b["opening_n"]},
        {"metric": "Team rating", "a": a["rating"], "b": b["rating"], "suffix": "",
         "dec": 2},
    ]
    # The largest map and side edges, already ranked and capped, mixed in with the
    # team-level rows and re-sorted by gap size.
    metrics.extend(_map_side_edges(conn, team_a, team_b, window, stage, pool))
    ranked = stats.rank_metric_gaps(metrics)
    rows = []
    any_noise = False
    for r in ranked:
        dec = r["dec"]
        fmt = pct100 if dec == 0 else num2
        leads = a_tag if r["leader"] == "a" else (
            b_tag if r["leader"] == "b" else "even")
        # When both sides carry a count, judge whether the gap is distinguishable
        # from noise by overlapping their Wilson bands. A blank cell means the row
        # has no count to build a band from (the rating, the map and side edges).
        overlap = None
        if "a_total" in r:
            overlap = stats.bands_overlap(
                r["a_won"], r["a_total"], r["b_won"], r["b_total"])
        if overlap:
            any_noise = True
        rows.append({
            "Metric": r["metric"],
            a_tag: fmt(r["a"]),
            b_tag: fmt(r["b"]),
            "Gap (A-B)": gap_str(r["a"], r["b"], r["suffix"], dec),
            "Leads": leads,
            # Only the overlap case is tagged; a blank means either the gap is
            # distinguishable or the row carries no sample to band (rating, edges).
            "Within noise": "bands overlap" if overlap else "",
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True)
    noise_note = (
        " A row tagged \"bands overlap\" has the two teams' 95% confidence bands "
        "crossing, so that gap is within sampling noise and should not be read as "
        "a real edge."
        if any_noise else
        " The \"within noise\" column tags a gap whose two confidence bands cross "
        "as not distinguishable; none did here."
    )
    st.caption(
        "Sorted by the size of the gap, biggest first, so what separates the "
        "teams sits on top and the near-ties at the bottom. Team-level figures sit "
        "alongside the largest map and side edges (cross-side duels on the likely "
        f"maps, over a real sample of at least {MIN_MAP_ROUNDS} rounds). The Leads "
        "column marks which team is higher on that one row; it is not a tally and "
        "never counts who leads more rows or calls a match winner." + noise_note
    )


def _team_tokens(team):
    """Casefolded name and tag tokens used to match a team against API text."""
    return {t for t in ((team["name"] or "").casefold(),
                        (team["tag"] or "").casefold()) if t}


def _detect_upcoming(team_a, team_b):
    """Look up an upcoming match between the two teams from the live API.

    Automates the manual tag when the two selected teams are scheduled to play.
    Matches the API's team names against each selected team's name or tag (either
    order). Returns {match_date, event_name, is_lan} on a hit, None when no match
    is scheduled, and raises on an API failure so the caller can fall back to
    manual entry. Depends on the API being up and its upcoming list being accurate,
    so it is strictly a convenience over the manual tag.
    """
    from valtrack.api_client import VlrClient

    a_tokens, b_tokens = _team_tokens(team_a), _team_tokens(team_b)
    client = VlrClient(request_delay=0)
    for seg in client.upcoming_matches():
        names = {(seg.get("team1") or "").casefold(),
                 (seg.get("team2") or "").casefold()}

        def hit(tokens):
            return any(tok and any(tok in n or n in tok for n in names if n)
                       for tok in tokens)

        if hit(a_tokens) and hit(b_tokens):
            event = seg.get("match_event") or ""
            match_date = None
            ts = seg.get("unix_timestamp")
            try:
                if ts:
                    match_date = dt.datetime.fromtimestamp(
                        int(float(ts)), tz=dt.timezone.utc).date().isoformat()
            except (ValueError, OSError, OverflowError):
                match_date = None
            return {"match_date": match_date, "event_name": event,
                    "is_lan": is_lan_event(event)}
    return None


def render_upcoming_schedule(conn, teams):
    """List scheduled franchise matches and load one into the comparison (P5).

    The whole job is predicting an upcoming match, yet the user otherwise picks
    both teams by hand. This pulls the live upcoming-matches feed (behind a button,
    since it needs the API up), keeps the franchise-versus-franchise meetings, and
    on a click loads that pair into the comparison and tags the date, event, and
    LAN flag, so the tool can be opened against this week's slate. A navigation
    convenience over the manual picker; it produces no judged output.
    """
    st.subheader("Upcoming franchise matches")
    if st.button(
        "Load the schedule from the API",
        help="Pull the live upcoming-matches feed and keep the franchise-versus-"
             "franchise meetings. Needs vlrggapi running.",
    ):
        try:
            from valtrack.api_client import VlrClient
            st.session_state["upcoming_segments"] = (
                VlrClient(request_delay=0).upcoming_matches())
        except Exception as exc:  # API down or unreachable: say so, keep manual flow.
            st.session_state.pop("upcoming_segments", None)
            st.warning(
                f"Could not reach the upcoming-matches API ({exc}). Start vlrggapi "
                "(python main.py in the vlrggapi folder) and try again.")

    segments = st.session_state.get("upcoming_segments")
    if segments is None:
        st.caption(
            "Click to pull the scheduled franchise-versus-franchise matches from "
            "the live feed and load one into the comparison in a click.")
        return
    pairs = schedule.franchise_upcoming(segments, teams)
    if not pairs:
        st.caption(
            "No franchise-versus-franchise matches are listed in the feed right "
            "now. Pick the two teams by hand, or check back closer to a match day.")
        return
    id_to_index = {t["id"]: i for i, t in enumerate(teams)}
    for i, p in enumerate(pairs):
        date_str = p["match_date"] or "date TBD"
        env = "LAN" if p["is_lan"] else "online/unknown"
        event = f" - {p['event']}" if p["event"] else ""
        left, right = st.columns([4, 1])
        left.write(
            f"**{p['a']['name']}** vs **{p['b']['name']}**  ({date_str}{event}, "
            f"{env})")
        if right.button("Load", key=f"sched_load_{i}"):
            st.session_state["team_a"] = id_to_index[p["a"]["id"]]
            st.session_state["team_b"] = id_to_index[p["b"]["id"]]
            journal.save_upcoming(
                conn, p["a"]["id"], p["b"]["id"], p["match_date"] or "",
                p["event"], p["is_lan"])
            st.rerun()
    st.caption(
        "Scheduled meetings from the live feed. Loading one selects the pair and "
        "tags the date, event, and LAN flag; the series format is only known once "
        "the match is played. Team names are matched to the franchise list, so a "
        "rare mismatch can drop a real meeting.")


def render_upcoming_tag(conn, team_a, team_b):
    """Tag the real upcoming match (date, event, LAN), feeding the context panel (item 26).

    The tag can be entered by hand or auto-detected from the live upcoming-matches
    endpoint when the two teams are scheduled to play. Auto-detection depends on
    the API being up, so manual entry stays as the fallback.
    """
    st.subheader("Upcoming match")
    current = journal.get_upcoming(conn, team_a["id"], team_b["id"])
    if st.button(
        "Auto-detect from API",
        help="Check the live upcoming-matches list for a scheduled meeting between "
             "these two teams and fill the tag. Needs vlrggapi running.",
    ):
        try:
            found = _detect_upcoming(team_a, team_b)
        except Exception as exc:  # API down or unreachable: fall back to manual.
            st.warning(
                f"Could not reach the upcoming-matches API ({exc}). Start vlrggapi "
                "or enter the match by hand below.")
        else:
            if found:
                journal.save_upcoming(
                    conn, team_a["id"], team_b["id"], found["match_date"] or "",
                    found["event_name"], found["is_lan"])
                st.success(
                    "Found a scheduled match"
                    + (f" on {found['match_date']}" if found["match_date"] else "")
                    + (f" ({found['event_name']})" if found["event_name"] else "")
                    + ". Tag saved.")
                st.rerun()
            else:
                st.info(
                    "No upcoming match between these two teams is listed right now. "
                    "Enter it by hand below if you know it.")
    with st.expander("Tag the upcoming match (optional)", expanded=False):
        with st.form(f"upcoming_{journal.pair_key(team_a['id'], team_b['id'])}"):
            d = st.date_input(
                "Match date",
                value=dt.date.fromisoformat(current["match_date"])
                if current and current.get("match_date") else dt.date.today(),
            )
            ev = st.text_input(
                "Event", value=current["event_name"] if current else "")
            lan = st.checkbox(
                "LAN event", value=bool(current and current.get("is_lan")))
            c1, c2 = st.columns(2)
            if c1.form_submit_button("Save tag"):
                journal.save_upcoming(
                    conn, team_a["id"], team_b["id"], d.isoformat(), ev, lan)
                st.success("Upcoming match tagged.")
                st.rerun()
            if c2.form_submit_button("Clear tag"):
                journal.clear_upcoming(conn, team_a["id"], team_b["id"])
                st.rerun()
    return current


def _adaptive_recent_window(conn, team_a, team_b, events, stage):
    """A recent dashboard window, widened from 3 to 6 months when it is too thin (P6).

    The dashboard defaults to the last 3 months, but for a team between events
    that can be only a handful of matches, so the headline figures sit under the
    small-sample floor and read as noise without the user choosing that. When
    either team has fewer than MIN_MATCHES decided in the last 3 months, this
    widens to 6 months and returns a note saying so, rather than silently showing
    a thin window. The manual range control is untouched.

    Returns (window, note).
    """
    k = _db_key()
    today = dt.date.today()
    w90 = DateWindow(today - dt.timedelta(days=90), today)
    a90 = cq_record(k, conn, team_a["id"], w90, events, stage)["decided"]
    b90 = cq_record(k, conn, team_b["id"], w90, events, stage)["decided"]
    if min(a90, b90) < MIN_MATCHES:
        return (
            DateWindow(today - dt.timedelta(days=180), today),
            f"Widened to the last 6 months: the last 3 months held only {a90} and "
            f"{b90} decided matches for the two teams (under the {MIN_MATCHES}-match "
            "floor), so a 3-month default would read as noise. Turn off the recent "
            "toggle to use the range selected above.",
        )
    return w90, "Last 3 months."


def render_prematch_dashboard(conn, team_a, team_b, window, events, stage, five_only):
    """A matchup-first briefing assembled in the order a predictor reasons (item 21).

    A compact matchup card, then the map duel board, the context flags, and the
    biggest-difference view. Defaults to a recent window since all-time spans
    rosters and metas (item 25), with one click back to the selected range, and
    widens that recent window when 3 months is too thin (P6). It presents
    differences and context only, never a rating or a who-wins call.
    """
    st.caption(
        "A matchup-first view: the card, the maps, the honesty flags, and the "
        "biggest gaps, in the order you would reason through a match. Differences "
        "and context only, never a prediction."
    )
    upcoming = render_upcoming_tag(conn, team_a, team_b)

    use_recent = st.checkbox(
        "Use a recent window for this view", value=True, key="dash_recent",
        help=(
            "All-time data spans roster changes and old metas, which is the wrong "
            "default for a prediction. This narrows the dashboard to a recent "
            "window (3 months, widened to 6 when that is too thin); turn it off to "
            "use the range selected above."
        ),
    )
    if use_recent:
        dash_window, recent_note = _adaptive_recent_window(
            conn, team_a, team_b, events, stage)
        st.caption(recent_note)
    else:
        dash_window = window

    st.divider()
    render_coverage_strip(conn, team_a, team_b, dash_window, stage)
    st.divider()
    card_left, card_right = st.columns(2)
    for col, team in ((card_left, team_a), (card_right, team_b)):
        with col:
            st.markdown(f"### {team['name']}")
            if team["logo"]:
                st.image(team["logo"], width=64)
            rec = cq_record(_db_key(), conn, team["id"], dash_window, events, stage)
            st.metric("Record", f"{rec['wins']}-{rec['losses']}")
            results = cq_results(
                _db_key(), conn, team["id"], dash_window, events, stage)
            fs = stats.form_and_streak(results)
            if fs["decided"]:
                st.markdown("Form: " + color_form(fs["form"]))
                render_form_sparkline(results, key=f"spark_dash_{team['id']}")
            else:
                st.caption("No decided matches in this window.")

    h2h = cq_h2h(
        _db_key(), conn, team_a["id"], team_b["id"], dash_window, events, stage)
    st.caption(
        f"Head-to-head in window: {team_a['name']} {h2h['a_wins']} - "
        f"{h2h['b_wins']} {team_b['name']} ({h2h['decided']} meetings)."
    )

    st.divider()
    render_context_panel(conn, team_a, team_b, dash_window, events, stage, upcoming)
    st.divider()
    pool = _likely_pool(conn, team_a, team_b, dash_window)
    render_duel_board(conn, team_a, team_b, dash_window, stage, pool)
    st.divider()
    render_gap_view(
        conn, team_a, team_b, dash_window, events, stage, five_only, pool)


def render_favorites(conn, teams):
    """Save the current pair and one-click reload a saved one (item 18).

    A small local list next to the notes and log. Loading a favorite seeds the two
    team picks and reruns, so a revisited matchup is one click rather than two
    dropdowns or an old link.
    """
    id_to_index = {t["id"]: i for i, t in enumerate(teams)}
    favs = journal.list_favorites(conn)
    if not favs:
        st.caption("No saved matchups yet. Use the star on the comparison to save one.")
        return
    for f in favs:
        if f["team_a_id"] not in id_to_index or f["team_b_id"] not in id_to_index:
            continue
        c1, c2 = st.columns([4, 1])
        c1.write(f"{f['team_a_name']} vs {f['team_b_name']}")
        if c2.button("Load", key=f"fav_load_{f['pair_key']}"):
            st.session_state["team_a"] = id_to_index[f["team_a_id"]]
            st.session_state["team_b"] = id_to_index[f["team_b_id"]]
            st.rerun()


def render_sticky_header(team_a, team_b):
    """A compact pinned strip naming the two teams, kept visible on scroll (item 19).

    The two-column layout loses track of which side is which deep in the page, so
    this fixes a small header at the top via a sticky CSS block. Streamlit's
    support for this is limited, so it is a light shim, not a guarantee on every
    browser.
    """
    a = f"{team_a['name']} ({team_a['tag']})" if team_a["tag"] else team_a["name"]
    b = f"{team_b['name']} ({team_b['tag']})" if team_b["tag"] else team_b["name"]
    st.markdown(
        f"""
        <div style="position:sticky;top:0;z-index:999;background:rgba(14,17,23,0.95);
                    padding:6px 10px;border-bottom:1px solid #333;margin-bottom:6px;">
          <b>{a}</b> &nbsp;vs&nbsp; <b>{b}</b>
        </div>
        """,
        unsafe_allow_html=True,
    )


def run_incremental_refresh():
    """Pull new matches since the last update and detail the newest of them.

    Reuses the ingestion engine in incremental scope, so it
    stops at matches already stored and never runs the full all-time harvest. The
    detail pass is capped so a click stays quick. The engine stamps last_updated
    and last_status itself; on an API failure it records "failed" and raises,
    which we surface as the API likely being down.
    """
    with st.status("Refreshing data (incremental)...", expanded=True) as status:
        try:
            status.write("Pulling new matches (cheap pass)...")
            cheap = ingest.run_ingest(scope="incremental", progress=status.write)
            status.write(
                f"Found {cheap['matches']} new match rows across "
                f"{cheap['teams']} teams."
            )
            status.write("Fetching detail for the newest matches...")
            details = ingest.run_detail_ingest(
                scope="incremental", limit=REFRESH_DETAIL_LIMIT,
                progress=status.write,
            )
            # New rows are stored, so the cached reads are stale: clear them so
            # the views recompute against the fresh data on the next rerun.
            st.cache_data.clear()
            status.update(label="Refresh complete", state="complete")
            st.success(
                f"Refreshed: {cheap['matches']} new match rows, "
                f"{details['matches']} matches detailed."
            )
        except Exception as exc:
            status.update(label="Refresh failed", state="error")
            st.error(
                f"Refresh failed: {exc}. The data API may be down. Start vlrggapi "
                "(python main.py in the vlrggapi folder) and try again."
            )


def render_freshness(conn):
    """Show the refresh button and the staleness banner in its two states.

    One state warns that the stored data is older than the threshold (the user
    has not refreshed recently); the other warns that the last refresh attempt
    failed, so the API may be down. Both read the meta bookkeeping the ingestion
    writes.
    """
    last_status = db.get_meta(conn, "last_status")
    last_updated = db.get_meta(conn, "last_updated")
    age = freshness.age_days(last_updated)

    left, right = st.columns([1, 4])
    with left:
        if st.button("Refresh data", help="Incremental update, not the full harvest."):
            run_incremental_refresh()
    with right:
        if last_status == "failed":
            st.error(
                "The most recent refresh attempt failed, so the data API may be "
                "down. The stored data still works for viewing."
            )
        elif age is None:
            st.warning("No refresh recorded yet. Click Refresh to pull new matches.")
        elif age >= STALE_REFRESH_DAYS:
            st.warning(
                f"Stored data was last updated {age:.0f} days ago. Click Refresh "
                "to pull newer matches."
            )
        else:
            note = f"Data updated {age:.0f} days ago."
            if last_status == "partial":
                note += " The last refresh finished with some errors."
            st.caption(note)


WINDOW_MODES = ["All time", "Last 3 months", "Last 6 months", "Year to date",
                "Current split (approx)", "Since last roster change",
                "Custom range"]
ENV_MODES = ["All", "International LAN", "Online/other"]
STAGE_MODES = ["All", "Group / swiss", "Playoff / elimination"]
VIEW_MODES = ["Side by side", "Aligned"]


def _apply_url_state(teams):
    """Seed the widget defaults from the URL once, so a link restores the view.

    The selection, window, and toggles are encoded in the query string (item 16),
    so a refresh or a bookmarked local link reopens the same comparison instead of
    resetting to the default pair. Seeding runs once per session, before the
    widgets are created, by writing their session_state keys; after that the
    widgets own their state. Unknown or malformed values are ignored rather than
    forced, so a hand-edited URL never crashes the app.
    """
    if st.session_state.get("_url_seeded"):
        return
    st.session_state["_url_seeded"] = True
    params = st.query_params
    id_to_index = {t["id"]: i for i, t in enumerate(teams)}

    def seed_team(param, key):
        raw = params.get(param)
        try:
            tid = int(raw) if raw is not None else None
        except ValueError:
            return
        if tid in id_to_index:
            st.session_state[key] = id_to_index[tid]

    seed_team("a", "team_a")
    seed_team("b", "team_b")
    if params.get("win") in WINDOW_MODES:
        st.session_state["dwmode"] = params["win"]
    if params.get("env") in ENV_MODES:
        st.session_state["env"] = params["env"]
    if params.get("stage") in STAGE_MODES:
        st.session_state["stage"] = params["stage"]
    if params.get("view") in VIEW_MODES:
        st.session_state["view"] = params["view"]
    if params.get("five") in ("0", "1"):
        st.session_state["five"] = params["five"] == "1"
    if params.get("pal") in PALETTES:
        st.session_state["palette"] = params["pal"]
    # The section picker (item 17): a comma-joined list of section indices, so a
    # bookmarked link restores which sections were shown. Out-of-range indices are
    # ignored rather than crashing a hand-edited URL.
    sec = params.get("sec")
    if sec is not None:
        chosen = []
        for tok in sec.split(","):
            if tok.isdigit() and int(tok) < len(TEAM_SECTIONS):
                chosen.append(TEAM_SECTIONS[int(tok)])
        if chosen:
            st.session_state["sections"] = chosen


def _write_url_state(teams, a, b):
    """Reflect the current selection back into the URL (item 16).

    Written only when it actually changed, so a settled selection does not loop
    the app rerunning itself. The window, event, and view read their own widget
    keys, which exist by the time this runs.
    """
    desired = {
        "a": str(teams[a]["id"]),
        "b": str(teams[b]["id"]),
        "win": st.session_state.get("dwmode", "All time"),
        "env": st.session_state.get("env", "All"),
        "stage": st.session_state.get("stage", "All"),
        "view": st.session_state.get("view", "Side by side"),
        "five": "1" if st.session_state.get("five") else "0",
        "pal": st.session_state.get("palette", DEFAULT_PALETTE),
    }
    # Only carry the section list when it differs from the full default, to keep
    # the URL short for the common case (item 17).
    chosen = st.session_state.get("sections")
    if chosen and set(chosen) != set(TEAM_SECTIONS):
        idx = [str(i) for i, s in enumerate(TEAM_SECTIONS) if s in chosen]
        desired["sec"] = ",".join(idx)
    if dict(st.query_params) != desired:
        st.query_params.from_dict(desired)


def _swap_teams():
    """Exchange the two team picks, the one-click swap (item 12).

    Runs as a widget callback, before the rerun creates the selectboxes, so
    swapping their stored indices is all it takes to flip which team sits left.
    """
    st.session_state["team_a"], st.session_state["team_b"] = (
        st.session_state.get("team_b"), st.session_state.get("team_a"),
    )


# The widget keys the reset control clears to return to the default view (item
# 14). Clearing them and the URL drops the custom range, event filter,
# current-five toggle, palette, section picker, and league filter back to default.
_RESET_KEYS = (
    "team_a", "team_b", "dwmode", "dwrange", "env", "stage", "view", "five",
    "pool_only", "sections", "palette", "leagues_filter", "dash_recent",
    "_url_seeded",
)


def _reset_view():
    """Clear the view controls back to the default comparison (item 14)."""
    for key in _RESET_KEYS:
        st.session_state.pop(key, None)
    st.query_params.clear()


def main():
    st.title("VALTrack")
    st.caption(
        "VCT franchise team comparison. Pick two teams and a date range to see "
        "their data side by side."
    )

    if not db.DB_PATH.exists():
        st.error("No database found. Run the data harvest first with: python harvest.py")
        return

    conn = db.connect()
    db.ensure_app_tables(conn)
    db.ensure_analytics_tables(conn)  # per-match performance and economy tables
    db.ensure_columns(conn)  # self-heal an older database missing newer columns
    db.backfill_match_stage(conn)  # classify any match missing a stage label
    try:
        render_freshness(conn)
        st.divider()

        teams = queries.list_teams(conn)
        if len(teams) < 2:
            st.warning("The database holds fewer than two teams. Run the harvest first.")
            return

        _apply_url_state(teams)
        # Seed the default pick in session_state (unless the URL already did), so
        # the selectboxes can rely on the key without also passing an index, which
        # would otherwise clash with the session_state seeding.
        st.session_state.setdefault("team_a", 0)
        st.session_state.setdefault("team_b", min(1, len(teams) - 1))
        labels = [team_label(t) for t in teams]

        # League filter for the pickers (item 10). The list is already grouped by
        # league (list_teams orders by league then rank), and this narrows both
        # pickers to the chosen leagues. The options stay indices into the full
        # team list so swap and the URL state are unaffected; a pick that falls
        # outside the filter is snapped back to a valid option first.
        present = sorted({t["league"] for t in teams})
        leagues = st.multiselect(
            "Leagues", present, default=present, key="leagues_filter",
            format_func=lambda lg: lg.capitalize(),
            help="Filter both team pickers by league. Teams are grouped by league.",
        )
        allowed = set(leagues) if leagues else set(present)
        opts = [i for i in range(len(teams)) if teams[i]["league"] in allowed]
        if st.session_state.get("team_a") not in opts:
            st.session_state["team_a"] = opts[0]
        if st.session_state.get("team_b") not in opts:
            st.session_state["team_b"] = opts[-1]

        pick_left, pick_right = st.columns(2)
        with pick_left:
            a = st.selectbox(
                "Team A", opts, format_func=lambda k: labels[k], key="team_a",
            )
        with pick_right:
            b = st.selectbox(
                "Team B", opts, format_func=lambda k: labels[k], key="team_b",
            )
        ctrl_swap, ctrl_fav, ctrl_reset, ctrl_pal = st.columns([1, 1, 1, 2])
        with ctrl_swap:
            st.button(
                "Swap A and B", on_click=_swap_teams,
                help="Flip which team sits on the left without re-picking both.",
            )
        with ctrl_fav:
            saved = journal.is_favorite(conn, teams[a]["id"], teams[b]["id"])
            if saved:
                if st.button("★ Saved", help="Remove from saved matchups."):
                    journal.remove_favorite(conn, teams[a]["id"], teams[b]["id"])
                    st.rerun()
            elif st.button("☆ Save", help="Save this matchup for one-click reload."):
                journal.add_favorite(
                    conn, teams[a]["id"], teams[a]["name"],
                    teams[b]["id"], teams[b]["name"])
                st.rerun()
        with ctrl_reset:
            st.button(
                "Reset view", on_click=_reset_view,
                help="Clear the range, filters, and toggles back to default.",
            )
        with ctrl_pal:
            st.selectbox(
                "Color palette", list(PALETTES), key="palette",
                help="Colorblind-safe swaps the green/red cues for blue/orange.",
            )

        window = choose_window(conn, teams[a]["id"], teams[b]["id"])
        five_only = st.checkbox(
            "Current five only (player figures)",
            key="five",
            help=(
                "Narrows the player statistics, opening duels, and player-versus-"
                "player view to each team's current five. Team, map, and round "
                "figures stay over everyone who played, since a past round cannot "
                "be reassigned to the current roster."
            ),
        )
        pool_only = st.checkbox(
            "Current map pool only (map tables and veto)",
            key="pool_only",
            help=(
                "Hides maps that have left the current rotation (derived from the "
                "last 90 days of play across all teams) from the map tables and the "
                "veto reconstruction, so an all-time window does not surface a map "
                "that cannot be played now. Off by default; when off, an "
                "out-of-rotation map is marked rather than hidden."
            ),
        )
        env = st.radio(
            "Event type",
            ENV_MODES,
            horizontal=True,
            key="env",
            help=(
                "LAN versus online is inferred from event names, so it is best-"
                "effort. It narrows the match-level figures: record, form, and "
                "recent matches. The event name comes from the per-match detail, "
                "so a match without detail has an unknown environment and is "
                "counted only under All, not LAN or Online."
            ),
        )
        events = EventFilter(
            {"All": "all", "International LAN": "lan", "Online/other": "online"}[env]
        )
        if env != "All":
            st.caption(
                "LAN and Online cover only matches whose event is known (those "
                "with per-match detail harvested). As the detail harvest fills "
                "in, this split covers more matches."
            )

        stage_label = st.radio(
            "Stage",
            STAGE_MODES,
            horizontal=True,
            key="stage",
            help=(
                "Group/swiss versus playoff/elimination, classified from the "
                "bracket round label, so it is best-effort. It narrows every "
                "windowed figure, including the map and side splits, to that stage "
                "of play. A match whose round label cannot be placed is left out of "
                "both stages and counted only under All."
            ),
        )
        stage = StageFilter(
            {"All": "all", "Group / swiss": "group",
             "Playoff / elimination": "playoff"}[stage_label]
        )
        if stage_label != "All":
            st.caption(
                "Group/swiss and playoff/elimination cover only matches whose "
                "round label could be classified; ambiguous labels are left out of "
                "both, so the two stages need not sum to the All total."
            )

        mn, mx = queries.match_date_bounds(conn)
        if window.is_all_time:
            span_start, span_end = mn, mx
        else:
            span_start = window.start.isoformat() if window.start else mn
            span_end = window.end.isoformat() if window.end else mx
        span = eras.patch_era_span(span_start, span_end)
        if span:
            st.info(
                f"Patch era (rough): the displayed data spans {span}. Older data "
                "may reflect different maps, agents, and metas, so read across a "
                "wide range with care."
            )

        team_a = queries.get_team(conn, teams[a]["id"])
        team_b = queries.get_team(conn, teams[b]["id"])
        if team_a["id"] == team_b["id"]:
            st.warning("Pick two different teams to compare.")
            return

        _write_url_state(teams, a, b)

        render_sticky_header(team_a, team_b)
        # The likely-played pool, computed once, so the map tables can mark the
        # relevant maps (item 20) consistently with the veto reconstruction.
        highlight = set(_likely_pool(conn, team_a, team_b, window) or [])
        # The de-facto current rotation, for the map-pool guardrail (P2). Empty
        # when the database has no recent maps, in which case the filter no-ops.
        current_pool = set(cq_map_pool(_db_key(), conn))

        st.divider()
        tab_dash, tab_teams, tab_matchup, tab_notes = st.tabs(
            ["Pre-match", "Team comparison", "Matchup", "Notes and log"]
        )
        with tab_dash:
            render_upcoming_schedule(conn, teams)
            st.divider()
            render_prematch_dashboard(
                conn, team_a, team_b, window, events, stage, five_only)
        with tab_teams:
            view = st.radio(
                "View", VIEW_MODES, horizontal=True, key="view",
                help=(
                    "Side by side shows each team's full column. Aligned shows one "
                    "shared table per stat with the gap between the teams."
                ),
            )
            render_comparison_strip(
                conn, team_a, team_b, window, events, stage, five_only)
            render_league_reference(conn, teams, team_a, team_b, window, events, stage)
            render_glossary()
            st.divider()
            if view == "Aligned":
                # Jump-to-section nav (item v4.4). Only the aligned view has unique
                # subheader anchors; the side-by-side view duplicates them across
                # the two columns, so the nav is shown for the aligned view only.
                st.markdown(
                    "Jump to: [Core figures](#core-figures-aligned) | "
                    "[Map and side splits](#per-map-and-side-win-rates-aligned)"
                )
                render_aligned(conn, team_a, team_b, window, events, stage,
                               five_only, current_pool, pool_only)
            else:
                sections = st.multiselect(
                    "Sections to show", TEAM_SECTIONS, default=TEAM_SECTIONS,
                    key="sections",
                    help="Hide sections to focus the column on what you want.",
                )
                show_left, show_right = st.columns(2)
                render_team(conn, show_left, team_a, window, five_only, events,
                            stage, sections, highlight, current_pool, pool_only)
                render_team(conn, show_right, team_b, window, five_only, events,
                            stage, sections, highlight, current_pool, pool_only)
        with tab_matchup:
            # A small jump-to-section nav (item 16). Streamlit auto-anchors each
            # subheader from its text, so these links scroll to them.
            st.markdown(
                "Jump to: [Veto](#veto-and-map-pool-reconstruction) | "
                "[Map breakdown](#map-by-map-breakdown) | "
                "[Head-to-head](#head-to-head) | "
                "[Player vs player](#player-versus-player) | "
                "[Common opponents](#common-opponents)"
            )
            render_veto_reconstruction(conn, team_a, team_b, window, stage,
                                       current_pool, pool_only)
            render_map_breakdown(conn, team_a, team_b, window, events, stage)
            render_head_to_head(conn, team_a, team_b, window, events, stage)
            render_player_vs_player(conn, team_a, team_b, window, stage, five_only)
            render_common_opponents(conn, team_a, team_b, window, events, stage)
        with tab_notes:
            st.subheader("Saved matchups")
            render_favorites(conn, teams)
            render_notes(conn, team_a, team_b)
            render_matchup_log(conn, team_a, team_b)
    finally:
        conn.close()


main()
