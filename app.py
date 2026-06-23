"""VALTrack: two-team comparison view with a shared date range (Build Step 4).

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

from valtrack import db, eras, freshness, ingest, journal, queries, stats, veto
from valtrack.window import DateWindow, EventFilter, is_lan_event

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

FLAG = "⚠"        # the small-sample marker shown next to a thin figure

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


@st.cache_data(show_spinner=False)
def cq_record(db_key, _conn, team_id, window, events):
    return queries.team_record(_conn, team_id, window, events)


@st.cache_data(show_spinner=False)
def cq_results(db_key, _conn, team_id, window, events):
    return queries.decided_results(_conn, team_id, window, events)


@st.cache_data(show_spinner=False)
def cq_recent(db_key, _conn, team_id, window, events, limit):
    return queries.recent_matches(_conn, team_id, window, limit=limit, events=events)


@st.cache_data(show_spinner=False)
def cq_sos(db_key, _conn, team_id, window, events):
    return queries.schedule_strength(_conn, team_id, window, events)


@st.cache_data(show_spinner=False)
def cq_map_results(db_key, _conn, team_id, window):
    return _dicts(queries.team_map_results(_conn, team_id, window))


@st.cache_data(show_spinner=False)
def cq_rounds(db_key, _conn, team_id, window):
    return _dicts(queries.team_rounds(_conn, team_id, window))


@st.cache_data(show_spinner=False)
def cq_player_opening(db_key, _conn, team_id, window):
    return _dicts(queries.team_player_opening(_conn, team_id, window))


@st.cache_data(show_spinner=False)
def cq_player_stats(db_key, _conn, team_id, window):
    return _dicts(queries.team_player_stats(_conn, team_id, window))


@st.cache_data(show_spinner=False)
def cq_appearances(db_key, _conn, team_id, window):
    return _dicts(queries.player_appearances(_conn, team_id, window))


@st.cache_data(show_spinner=False)
def cq_vetos(db_key, _conn, team_id, window):
    return _dicts(queries.team_vetos(_conn, team_id, window))


@st.cache_data(show_spinner=False)
def cq_series(db_key, _conn, team_id, window):
    return _dicts(queries.team_series_results(_conn, team_id, window))


@st.cache_data(show_spinner=False)
def cq_h2h(db_key, _conn, a_id, b_id, window, events):
    return queries.head_to_head(_conn, a_id, b_id, window, events)


@st.cache_data(show_spinner=False)
def cq_common(db_key, _conn, a_id, b_id, window, events):
    return queries.common_opponents(_conn, a_id, b_id, window, events)


@st.cache_data(show_spinner=False)
def cq_coverage(db_key, _conn, team_id, window, events):
    return queries.detail_coverage(_conn, team_id, window, events)


@st.cache_data(show_spinner=False)
def cq_window_summary(db_key, _conn, team_id, window, events):
    return queries.team_window_summary(_conn, team_id, window, events)


@st.cache_data(show_spinner=False)
def cq_meeting_maps(db_key, _conn, match_id):
    return _dicts(queries.meeting_maps(_conn, match_id))


@st.cache_data(show_spinner=False)
def cq_meeting_lineup(db_key, _conn, match_id, team_name):
    return queries.meeting_lineup(_conn, match_id, team_name)


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


def choose_window(conn):
    """Render the shared date-range control and return a DateWindow.

    All time is the default and applies no filter. A custom range is bounded by
    the earliest and latest stored match dates. The same window drives both
    teams, so the comparison stays aligned.
    """
    mn, mx = queries.match_date_bounds(conn)
    today = dt.date.today()
    mode = st.radio(
        "Date range",
        ["All time", "Last 3 months", "Last 6 months", "Year to date",
         "Custom range"],
        horizontal=True,
        key="dwmode",
        help=(
            "Windowed figures (record, recent matches, form and streak) "
            "recompute for the chosen range. The presets are quick spans relative "
            "to today; pick Custom range for an exact window. Ranking, rating, and "
            "earnings are VLR's current all-time values and do not change."
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


def render_form_sparkline(results):
    """A small Plotly sparkline of the running win-loss differential.

    `results` is the decided results newest first. We chart the last stretch in
    chronological order as a cumulative net (each win +1, each loss -1), so an
    upward line is a team trending up. It is a shape, not a number, which is the
    point of a sparkline.
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
    st.plotly_chart(fig, width="stretch")


def color_form(results):
    """Recent results as colored letters, green for a win and red for a loss."""
    return " ".join(
        f":green[{r}]" if r == "W" else f":red[{r}]" for r in results
    )


def render_record_and_form(conn, team, window, events):
    k = _db_key()
    record = cq_record(k, conn, team["id"], window, events)
    if record["decided"]:
        winpct = f"{100 * record['wins'] / record['decided']:.0f}%"
    else:
        winpct = "n/a"
    st.metric(f"Record ({window.label})", f"{record['wins']}-{record['losses']}")
    flag = flag_if_small(record["decided"], MIN_MATCHES)
    st.caption(f"{record['decided']} decided matches, win rate {winpct}{flag}")

    sos = cq_sos(k, conn, team["id"], window, events)
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

    results = cq_results(k, conn, team["id"], window, events)
    fs = stats.form_and_streak(results)
    if fs["decided"]:
        st.markdown("**Form** (most recent first): " + color_form(fs["form"]))
        st.write(f"**Current streak:** {fs['streak_kind']}{fs['streak_len']}")
        render_form_sparkline(results)
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


def render_map_splits(conn, team, window):
    """Per-map win rate with attack and defense side splits for the window.

    Computed from the stored rounds, so it only has figures for maps whose
    per-match detail has been harvested. When none is stored for this team in the
    range, say so plainly rather than show an empty table.
    """
    st.divider()
    st.subheader("Per-map and side win rates")
    k = _db_key()
    map_rows = cq_map_results(k, conn, team["id"], window)
    round_rows = cq_rounds(k, conn, team["id"], window)
    table = stats.per_map_splits(map_rows, round_rows, team["name"])
    if not table:
        st.caption(DETAIL_EMPTY)
        return
    rows = []
    for m in table:
        decided = m["won"] + m["lost"]
        flag = flag_if_small(m["rounds_total"], MIN_MAP_ROUNDS)
        rows.append({
            "Map": m["map_name"] + flag,
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
        st.plotly_chart(fig, width="stretch")

    st.caption(
        f"Map win% is over decided maps. Side win rates are over rounds played "
        f"on that side. Round and map counts are shown so a small sample is "
        f"visible; {FLAG} marks a map with fewer than {MIN_MAP_ROUNDS} rounds."
    )


def render_pistol(conn, team, window):
    """Team-level pistol-round win rate with attack and defense splits.

    Computed from the stored rounds (round 1 and round 13 of each map), so it
    only has figures where per-match detail has been harvested. The won/total
    sample is shown so a thin pistol sample is visible. Economy conversion (eco
    and anti-eco) is not shown: the data source returns broken per-map economy,
    so those figures are deferred rather than guessed.
    """
    st.divider()
    st.subheader("Pistol rounds")
    round_rows = cq_rounds(_db_key(), conn, team["id"], window)
    p = stats.pistol_winrate(round_rows, team["name"])
    if p["total"] == 0:
        st.caption(DETAIL_EMPTY)
        return
    overall, atk, defense = st.columns(3)
    overall.metric("Pistol win%", pct(p["winrate"]), help=f"{p['won']} of {p['total']}")
    atk.metric(
        "ATK pistol%", pct(p["atk_winrate"]), help=f"{p['atk_won']} of {p['atk_total']}"
    )
    defense.metric(
        "DEF pistol%", pct(p["def_winrate"]), help=f"{p['def_won']} of {p['def_total']}"
    )
    small = flag_if_small(p["total"], MIN_PISTOLS)
    st.caption(
        f"Pistol win rate over {p['total']} pistol rounds{small} (round 1 and "
        f"round 13 of each map){'; small sample' if small else ''}. Eco and "
        "anti-eco conversion are not shown: the data source returns broken per-map "
        "economy, so those figures are deferred until it is fixed."
    )


def render_opening(conn, team, window, five_names=None):
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
        cq_player_opening(_db_key(), conn, team["id"], window), five_names
    )
    o = stats.opening_duels(rows, team["name"])
    if o["duels"] == 0:
        st.caption(DETAIL_EMPTY)
        return
    overall, atk, defense = st.columns(3)
    overall.metric(
        "Opening-duel win%", pct(o["winrate"]),
        help=f"{o['fk']} first kills of {o['duels']} opening duels",
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
    st.caption(
        f"Opening-duel win rate is first kills over opening duels (first kills "
        f"plus first deaths). The attack and defense splits are per-side totals, "
        f"not a round-by-round timeline, since the source stores only per-map "
        f"first-kill and first-death counts. Duel counts are shown so a small "
        f"sample is visible; {FLAG} marks fewer than {MIN_DUELS} duels"
        f"{' (team total included)' if team_small else ''}."
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


def render_player_stats(conn, team, window, five_names=None):
    """Per-player aggregated statistics for the window, with a per-agent view.

    Computed from the stored per-map player lines, so it only has figures where
    per-match detail has been harvested. The rate stats (rating, ACS, ADR, KAST,
    headshot percentage) are round-weighted across the player's maps; K/D and the
    per-round figures are summed then divided. Maps and rounds are shown as the
    sample size. Clutch statistics are not shown: the data source does not expose
    them, so they are left out rather than guessed. When five_names is set the
    table is narrowed to the current five.
    """
    st.divider()
    st.subheader("Player statistics")
    rows = stats.keep_players(
        cq_player_stats(_db_key(), conn, team["id"], window), five_names
    )
    players = stats.player_aggregates(rows, team["name"])
    if not players:
        st.caption(DETAIL_EMPTY)
        return
    table = []
    for p in players:
        table.append({
            "Player": p["player_name"] + flag_if_small(p["maps"], MIN_PLAYER_MAPS),
            "Rating": p["rating"],
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
        "approximation, since the source stores only the per-map percentage. Maps "
        f"and rounds are shown so a small sample is visible; {FLAG} marks fewer "
        f"than {MIN_PLAYER_MAPS} maps. Clutch statistics are not available from "
        "the data source."
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


def _team_map_splits(conn, team, window):
    """Per-map splits for a team keyed by map name, for the win-rate payoff."""
    k = _db_key()
    table = stats.per_map_splits(
        cq_map_results(k, conn, team["id"], window),
        cq_rounds(k, conn, team["id"], window),
        team["name"],
    )
    return {m["map_name"]: m for m in table}


def render_veto_reconstruction(conn, team_a, team_b, window):
    """Reconstruct the likely map pool for the two teams and show map win rates.

    Aggregates each team's veto tendencies over the window, infers the active map
    pool, and reconstructs the probable picks, decider, and bans. For the maps
    likely to be played it then surfaces each team's map win rate with attack and
    defense side splits (from Build Step 6). This is built from veto history, not
    a real upcoming veto, and it makes no claim about who wins the match.
    """
    st.header("Veto and map-pool reconstruction")
    k = _db_key()
    a_tend = veto.team_tendencies(
        cq_vetos(k, conn, team_a["id"], window), team_a["tag"]
    )
    b_tend = veto.team_tendencies(
        cq_vetos(k, conn, team_b["id"], window), team_b["tag"]
    )
    pool = veto.active_pool(a_tend, b_tend)
    if not pool:
        st.caption(
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
            "Map": r["map"] + flag_if_small(seen, MIN_VETO_APPEAR),
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
    st.plotly_chart(fig, width="stretch")

    st.subheader("Win rates on the likely-played maps")
    a_splits = _team_map_splits(conn, team_a, window)
    b_splits = _team_map_splits(conn, team_b, window)
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

        win_rows = []
        for map_name in rec["likely_played"]:
            a_win, a_atk, a_def = split_cells(a_splits, map_name)
            b_win, b_atk, b_def = split_cells(b_splits, map_name)
            win_rows.append({
                "Map": map_name,
                f"{team_a['tag'] or 'A'} map%": a_win,
                f"{team_a['tag'] or 'A'} ATK": a_atk,
                f"{team_a['tag'] or 'A'} DEF": a_def,
                f"{team_b['tag'] or 'B'} map%": b_win,
                f"{team_b['tag'] or 'B'} ATK": b_atk,
                f"{team_b['tag'] or 'B'} DEF": b_def,
            })
        st.dataframe(pd.DataFrame(win_rows), hide_index=True)

    st.caption(
        "Reconstructed from each team's veto history in the selected range, not "
        "an actual upcoming veto. The pool is inferred from the maps seen most in "
        "that history (narrow the date range for the current rotation). Play "
        "likelihood is each team's pick rate minus ban rate, summed; it ranks "
        f"maps, it does not predict the match winner. Pick and ban rates are over "
        f"the matches each map was in the pool; {FLAG} marks a map seen in fewer "
        f"than {MIN_VETO_APPEAR} of the two teams' vetos combined."
    )

    st.divider()
    render_overlap(conn, team_a, team_b, window, pool)


def render_player_vs_player(conn, team_a, team_b, window, five_only):
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
            cq_player_stats(k, conn, team_a["id"], window), a_names
        ),
        team_a["name"],
    )
    b_players = stats.player_aggregates(
        stats.keep_players(
            cq_player_stats(k, conn, team_b["id"], window), b_names
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


def render_head_to_head(conn, team_a, team_b, window, events):
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
    h2h = cq_h2h(k, conn, team_a["id"], team_b["id"], window, events)
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

    five_a = current_five_set(conn, team_a)
    five_b = current_five_set(conn, team_b)
    for m in h2h["meetings"]:
        winner = team_a["name"] if m["winner"] == "a" else team_b["name"]
        env = "LAN" if is_lan_event(m["event"]) else "online/unknown"
        header = (
            f"{m['date']}  |  {m['a_score']}-{m['b_score']}  |  {winner} won  "
            f"|  {env}"
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


def render_common_opponents(conn, team_a, team_b, window, events):
    """Opponents both teams have faced, with each team's record against them."""
    st.divider()
    st.header("Common opponents")
    common = cq_common(
        _db_key(), conn, team_a["id"], team_b["id"], window, events
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
        if st.form_submit_button("Add to log"):
            journal.add_log_entry(
                conn, team_a["id"], team_a["name"], team_b["id"], team_b["name"],
                note, confidence,
            )
            st.success("Added to the log.")

    entries = journal.list_log_entries(conn)
    if not entries:
        st.caption("No log entries yet. Add one above to start tracking your calls.")
        return
    confidence_options = ["very low", "low", "medium", "high", "very high"]
    st.subheader(f"Past entries ({len(entries)})")
    for e in entries:
        with st.container(border=True):
            created = (e["created_at"] or "")[:10]
            st.write(
                f"**{e['team_a_name']} vs {e['team_b_name']}** "
                f"({created}), confidence: {e['confidence']}"
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
                st.caption(f"Outcome: {winner}{e['outcome'] or ''} (recorded {resolved})")
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


def render_recent(conn, team, window, events):
    st.divider()
    st.subheader("Recent matches")
    recent = cq_recent(_db_key(), conn, team["id"], window, events, 10)
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
    styled = df.style.map(
        lambda v: "color:#2a9d8f" if v == "W"
        else ("color:#e76f51" if v == "L" else ""),
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


def render_roster_timeline(conn, team, window):
    """Show when each player appeared, so roster changes over the range show.

    Derived from who actually played (the transactions endpoint is unreliable),
    so it is an appearance timeline rather than official join and leave dates.
    Players not in the current five are marked, which is the point: an all-time
    window can span several rosters, and this makes that visible.
    """
    st.divider()
    st.subheader("Roster timeline")
    rows = cq_appearances(_db_key(), conn, team["id"], window)
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


def team_headline(conn, team, window, events, five_names=None):
    """The comparable headline figures for one team in a window.

    Win rate, pistol rate, and opening-duel rate come back as 0..100 numbers (or
    None when there is nothing to judge), the record as text, and a single
    round-weighted team rating. These are the figures the at-a-glance strip, the
    aligned core table, and the recent-versus-window block all read, so they are
    computed in one place. They are shown beside the opponent's with the gap; none
    of them is a composite or a winner call.
    """
    k = _db_key()
    rec = cq_record(k, conn, team["id"], window, events)
    win = 100 * rec["wins"] / rec["decided"] if rec["decided"] else None
    p = stats.pistol_winrate(cq_rounds(k, conn, team["id"], window), team["name"])
    o = stats.opening_duels(
        stats.keep_players(
            cq_player_opening(k, conn, team["id"], window), five_names),
        team["name"],
    )
    players = stats.player_aggregates(
        stats.keep_players(
            cq_player_stats(k, conn, team["id"], window), five_names),
        team["name"],
    )
    return {
        "record": f"{rec['wins']}-{rec['losses']}",
        "decided": rec["decided"],
        "win": win,
        "pistol": pct_num(p["winrate"]),
        "pistol_n": p["total"],
        "opening": pct_num(o["winrate"]),
        "opening_n": o["duels"],
        "rating": stats.team_rating(players),
    }


def render_comparison_strip(conn, team_a, team_b, window, events, five_only):
    """A compact aligned row of the headline numbers with the gap (item 2).

    Before the detailed sections, this assembles the figures the user would
    otherwise have to scroll both columns to collect: win rate, pistol rate,
    opening-duel rate, and a single team rating, each shown for both teams with the
    A minus B gap. It is a per-statistic difference, never a tally of who leads.
    """
    five_a = current_five_set(conn, team_a) if five_only else None
    five_b = current_five_set(conn, team_b) if five_only else None
    a = team_headline(conn, team_a, window, events, five_a)
    b = team_headline(conn, team_b, window, events, five_b)
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


def render_window_summary(conn, team, window, events):
    """One line on how much data backs this column (items 14 and 19).

    States the decided and total matches and the date span in range up front, then
    how many of those matches carry per-match detail, so the user knows how
    complete a detail-derived figure is before reading it.
    """
    k = _db_key()
    s = cq_window_summary(k, conn, team["id"], window, events)
    cov = cq_coverage(k, conn, team["id"], window, events)
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


def render_recent_vs_window(conn, team, window, events, five_names=None):
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
    recent = team_headline(conn, team, recent_window, events, five_names)
    base = team_headline(conn, team, window, events, five_names)
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


def render_pressure(conn, team, window):
    """Decider, distance, and comeback figures under series pressure (item 9).

    How a team does when a series is on the line: its win rate on deciding maps,
    its series win rate when a match reaches a decider, and how often it comes back
    from dropping the opening map. These are shown as separate figures, never
    folded into a single clutch or resilience rating, which would be the composite
    the charter forbids.
    """
    st.divider()
    st.subheader("Series pressure")
    rows = cq_series(_db_key(), conn, team["id"], window)
    ps = stats.pressure_stats(rows, team["name"])
    if ps["decider_played"] == 0 and ps["comeback_chances"] == 0:
        st.caption(
            "No multi-map series with a decider or an opening-map loss in this "
            "range yet. These fill in as the detail harvest runs."
        )
        return
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
        "decider map result and the series-in-decider result are closely related "
        "but shown separately, never combined into one rating. Comebacks count "
        "dropping the opening map and still winning the series."
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


def render_player_map_performance(conn, team, window, five_names=None):
    """Per-player statistics split by map, not just the all-map average (item 7).

    A duelist who pops off on Ascent but goes quiet on Lotus shows two different
    lines here, which is the sharpest axis in the game. The same round-weighted
    aggregation is reused per map, so the only new thing is the split. Per-map
    samples are thin, so the small-sample flag matters more here, not less.
    """
    st.divider()
    st.subheader("Player performance by map")
    rows = stats.keep_players(
        cq_player_stats(_db_key(), conn, team["id"], window), five_names
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


def render_overlap(conn, team_a, team_b, window, pool):
    """Where the two teams' per-map strengths collide or diverge (item 11).

    A strategic framing over numbers already computed: each team's per-map win
    rate, with the map marked shared strength, shared weakness, or split. It stays
    strictly descriptive and never ranks the maps into a veto verdict, which would
    be the call the charter forbids.
    """
    st.subheader("Map-pool overlap")
    a_splits = _team_map_splits(conn, team_a, window)
    b_splits = _team_map_splits(conn, team_b, window)
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


def render_aligned(conn, team_a, team_b, window, events, five_only):
    """One shared table per core stat with the gap, instead of two columns.

    This is the charter line the side-by-side layout under-delivers: each row shows
    A, B, and the gap, with maps in a single shared order so they line up (items 1
    and 5). It is a per-statistic difference throughout, never a cross-category
    tally or an overall rating.
    """
    five_a = current_five_set(conn, team_a) if five_only else None
    five_b = current_five_set(conn, team_b) if five_only else None
    a = team_headline(conn, team_a, window, events, five_a)
    b = team_headline(conn, team_b, window, events, five_b)
    a_tag = team_a["tag"] or "A"
    b_tag = team_b["tag"] or "B"

    st.subheader("Core figures, aligned")
    core = [
        {"Metric": "Win %", a_tag: pct100(a["win"]), b_tag: pct100(b["win"]),
         "Gap (A-B)": gap_str(a["win"], b["win"], "%"),
         "Sample": f"{a['decided']} vs {b['decided']} decided"},
        {"Metric": "Pistol %", a_tag: pct100(a["pistol"]),
         b_tag: pct100(b["pistol"]),
         "Gap (A-B)": gap_str(a["pistol"], b["pistol"], "%"),
         "Sample": f"{a['pistol_n']} vs {b['pistol_n']} pistols"},
        {"Metric": "Opening-duel %", a_tag: pct100(a["opening"]),
         b_tag: pct100(b["opening"]),
         "Gap (A-B)": gap_str(a["opening"], b["opening"], "%"),
         "Sample": f"{a['opening_n']} vs {b['opening_n']} duels"},
        {"Metric": "Team rating", a_tag: num2(a["rating"]),
         b_tag: num2(b["rating"]),
         "Gap (A-B)": gap_str(a["rating"], b["rating"], "", 2),
         "Sample": "round-weighted"},
    ]
    st.dataframe(pd.DataFrame(core), hide_index=True)

    st.subheader("Per-map and side win rates, aligned")
    a_splits = _team_map_splits(conn, team_a, window)
    b_splits = _team_map_splits(conn, team_b, window)
    if not a_splits and not b_splits:
        st.caption(DETAIL_EMPTY)
        return
    names = set(a_splits) | set(b_splits)

    def plays(splits, name):
        m = splits.get(name)
        return (m["won"] + m["lost"]) if m else 0

    # A single shared order so the rows line up: most-played across both teams
    # first, then by name. This is item 5, folded into the aligned table.
    ordered = sorted(names, key=lambda n: (-(plays(a_splits, n)
                                            + plays(b_splits, n)), n))
    rows = []
    for name in ordered:
        am = a_splits.get(name)
        bm = b_splits.get(name)
        a_win = am["map_winrate"] if am else None
        b_win = bm["map_winrate"] if bm else None
        seen = ((am["rounds_total"] if am else 0)
                + (bm["rounds_total"] if bm else 0))
        rows.append({
            "Map": name + flag_if_small(seen, MIN_MAP_ROUNDS),
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

    st.dataframe(
        pd.DataFrame(rows),
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
        f"than {MIN_MAP_ROUNDS} rounds across both teams. The gap is A minus B in "
        "points; it is a per-row difference, not a tally."
    )


def render_glossary():
    """A small glossary of the stat abbreviations (item 15)."""
    with st.expander("Glossary of stat abbreviations"):
        st.markdown(
            "- **ACS**: average combat score per round.\n"
            "- **KAST**: percent of rounds with a kill, assist, survival, or "
            "trade.\n"
            "- **ADR**: average damage per round.\n"
            "- **K/D**: kills divided by deaths.\n"
            "- **KPR / APR**: kills and assists per round.\n"
            "- **HS%**: headshot percentage (round-weighted approximation).\n"
            "- **FK / FD, FKPR / FDPR**: first kills and first deaths, and those "
            "per round.\n"
            "- **ATK / DEF**: attack side and defense side.\n"
            "- **Rating**: VLR's composite per-round rating, round-weighted "
            "across maps.\n"
            "- **Opening duel**: the first kill or death of a round; the win rate "
            "is first kills over opening duels.\n"
            "- **Decider**: the final map of a series entered level on maps."
        )


# The optional per-team sections, in render order, that the section picker
# (item 18) can hide so the user can focus the long column on what they want.
TEAM_SECTIONS = [
    "Record and form",
    "Snapshot",
    "Map splits",
    "Pistol",
    "Opening duels",
    "Player stats",
    "Player by map",
    "Series pressure",
    "Recent vs window",
    "Roster timeline",
    "Recent matches",
    "Roster",
]


def render_team(conn, column, team, window, five_only, events, sections):
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
        render_window_summary(conn, team, window, events)
        five_names = current_five_set(conn, team) if five_only else None

        def on(name):
            return name in sections

        if on("Record and form"):
            render_record_and_form(conn, team, window, events)
        if on("Snapshot"):
            render_snapshot(team)
        if on("Map splits"):
            render_map_splits(conn, team, window)
        if on("Pistol"):
            render_pistol(conn, team, window)
        if on("Opening duels"):
            render_opening(conn, team, window, five_names)
        if on("Player stats"):
            render_player_stats(conn, team, window, five_names)
        if on("Player by map"):
            render_player_map_performance(conn, team, window, five_names)
        if on("Series pressure"):
            render_pressure(conn, team, window)
        if on("Recent vs window"):
            render_recent_vs_window(conn, team, window, events, five_names)
        if on("Roster timeline"):
            render_roster_timeline(conn, team, window)
        if on("Recent matches"):
            render_recent(conn, team, window, events)
        if on("Roster"):
            render_roster(conn, team)


def run_incremental_refresh():
    """Pull new matches since the last update and detail the newest of them.

    Reuses the ingestion engine in incremental scope (Build Steps 2 and 5), so it
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
                "Custom range"]
ENV_MODES = ["All", "International LAN", "Online/other"]
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
    if params.get("view") in VIEW_MODES:
        st.session_state["view"] = params["view"]
    if params.get("five") in ("0", "1"):
        st.session_state["five"] = params["five"] == "1"


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
        "view": st.session_state.get("view", "Side by side"),
        "five": "1" if st.session_state.get("five") else "0",
    }
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
    db.ensure_columns(conn)  # self-heal an older database missing newer columns
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
        pick_left, pick_right = st.columns(2)
        with pick_left:
            a = st.selectbox(
                "Team A", range(len(teams)),
                format_func=lambda k: labels[k], key="team_a",
            )
        with pick_right:
            b = st.selectbox(
                "Team B", range(len(teams)),
                format_func=lambda k: labels[k], key="team_b",
            )
        st.button(
            "Swap A and B", on_click=_swap_teams,
            help="Flip which team sits on the left without re-picking both.",
        )

        window = choose_window(conn)
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

        st.divider()
        tab_teams, tab_matchup, tab_notes = st.tabs(
            ["Team comparison", "Matchup", "Notes and log"]
        )
        with tab_teams:
            view = st.radio(
                "View", VIEW_MODES, horizontal=True, key="view",
                help=(
                    "Side by side shows each team's full column. Aligned shows one "
                    "shared table per stat with the gap between the teams."
                ),
            )
            render_comparison_strip(conn, team_a, team_b, window, events, five_only)
            render_glossary()
            st.divider()
            if view == "Aligned":
                render_aligned(conn, team_a, team_b, window, events, five_only)
            else:
                sections = st.multiselect(
                    "Sections to show", TEAM_SECTIONS, default=TEAM_SECTIONS,
                    key="sections",
                    help="Hide sections to focus the column on what you want.",
                )
                show_left, show_right = st.columns(2)
                render_team(conn, show_left, team_a, window, five_only, events,
                            sections)
                render_team(conn, show_right, team_b, window, five_only, events,
                            sections)
        with tab_matchup:
            render_veto_reconstruction(conn, team_a, team_b, window)
            render_head_to_head(conn, team_a, team_b, window, events)
            render_player_vs_player(conn, team_a, team_b, window, five_only)
            render_common_opponents(conn, team_a, team_b, window, events)
        with tab_notes:
            render_notes(conn, team_a, team_b)
            render_matchup_log(conn, team_a, team_b)
    finally:
        conn.close()


main()
