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
import streamlit as st

from valtrack import db, queries, stats
from valtrack.window import DateWindow

st.set_page_config(page_title="VALTrack", layout="wide")


def rank_text(rank):
    """Show a rank, or an honest placeholder when we have none. Never a guess."""
    return f"#{rank}" if rank is not None else "not ranked"


def team_label(team):
    return f"{team['name']} ({team['league'].capitalize()})"


def choose_window(conn):
    """Render the shared date-range control and return a DateWindow.

    All time is the default and applies no filter. A custom range is bounded by
    the earliest and latest stored match dates. The same window drives both
    teams, so the comparison stays aligned.
    """
    mn, mx = queries.match_date_bounds(conn)
    mode = st.radio(
        "Date range",
        ["All time", "Custom range"],
        horizontal=True,
        help=(
            "Windowed figures (record, recent matches, form and streak) "
            "recompute for the chosen range. Ranking, rating, and earnings are "
            "VLR's current all-time values and do not change with the range."
        ),
    )
    if mode == "All time" or mn is None:
        return DateWindow.all_time()

    min_d = dt.date.fromisoformat(mn)
    max_d = dt.date.fromisoformat(mx)
    picked = st.date_input(
        "Custom range",
        value=(min_d, max_d),
        min_value=min_d,
        max_value=max_d,
    )
    if isinstance(picked, (tuple, list)) and len(picked) == 2:
        return DateWindow(picked[0], picked[1])
    st.caption("Pick both a start and an end date to apply the range.")
    return DateWindow.all_time()


def render_record_and_form(conn, team, window):
    record = queries.team_record(conn, team["id"], window)
    if record["decided"]:
        winpct = f"{100 * record['wins'] / record['decided']:.0f}%"
    else:
        winpct = "n/a"
    st.metric(f"Record ({window.label})", f"{record['wins']}-{record['losses']}")
    st.caption(f"{record['decided']} decided matches, win rate {winpct}")

    fs = stats.form_and_streak(queries.decided_results(conn, team["id"], window))
    if fs["decided"]:
        st.write("**Form** (most recent first): " + " ".join(fs["form"]))
        st.write(f"**Current streak:** {fs['streak_kind']}{fs['streak_len']}")
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


def render_recent(conn, team, window):
    st.divider()
    st.subheader("Recent matches")
    recent = queries.recent_matches(conn, team["id"], window, limit=10)
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
    st.dataframe(pd.DataFrame(rows), hide_index=True)


def render_roster(conn, team):
    st.divider()
    st.subheader("Roster")
    roster = stats.classify_roster(queries.get_roster(conn, team["id"]))
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


def render_team(conn, column, team, window):
    """Render one team's full comparison column."""
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

        render_record_and_form(conn, team, window)
        render_snapshot(team)
        render_recent(conn, team, window)
        render_roster(conn, team)


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
    try:
        teams = queries.list_teams(conn)
        if len(teams) < 2:
            st.warning("The database holds fewer than two teams. Run the harvest first.")
            return

        labels = [team_label(t) for t in teams]
        pick_left, pick_right = st.columns(2)
        with pick_left:
            a = st.selectbox(
                "Team A", range(len(teams)), index=0,
                format_func=lambda k: labels[k], key="team_a",
            )
        with pick_right:
            b = st.selectbox(
                "Team B", range(len(teams)), index=1,
                format_func=lambda k: labels[k], key="team_b",
            )

        window = choose_window(conn)

        team_a = queries.get_team(conn, teams[a]["id"])
        team_b = queries.get_team(conn, teams[b]["id"])
        if team_a["id"] == team_b["id"]:
            st.warning("Pick two different teams to compare.")

        st.divider()
        show_left, show_right = st.columns(2)
        render_team(conn, show_left, team_a, window)
        render_team(conn, show_right, team_b, window)
    finally:
        conn.close()


main()
