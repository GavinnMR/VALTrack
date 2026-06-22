"""VALTrack: minimal two-team comparison shell (Build Step 3).

The first end-to-end slice. It reads the stored teams and matches from SQLite
and shows two franchise teams side by side: identity, ranking, and overall
match record. There is no date range, no derived split, and no winner call here;
those arrive in later build steps. This step exists to prove the pipeline from
SQLite through the app to the browser.

Run with: streamlit run app.py
"""
import streamlit as st

from valtrack import db, queries

st.set_page_config(page_title="VALTrack", layout="wide")


def rank_text(rank):
    """Show a rank, or an honest placeholder when we have none. Never a guess."""
    return f"#{rank}" if rank is not None else "not ranked"


def team_label(team):
    return f"{team['name']} ({team['league'].capitalize()})"


def render_team(conn, column, team):
    """Render one team's identity, ranking, and record into a layout column."""
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

        record = queries.team_record(conn, team["id"])
        st.metric("Overall record", f"{record['wins']}-{record['losses']}")
        st.caption(f"{record['decided']} decided matches stored")

        st.metric("Regional rank", rank_text(team["regional_rank"]))
        st.metric("World rank", rank_text(team["world_rank"]))
        if team["rating"]:
            st.metric("Rating", team["rating"])


def main():
    st.title("VALTrack")
    st.caption("VCT franchise team comparison. Pick two teams to see their data side by side.")

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

        # The dropdown rows are light; pull the full identity and ranking row
        # for each chosen team before rendering.
        team_a = queries.get_team(conn, teams[a]["id"])
        team_b = queries.get_team(conn, teams[b]["id"])
        if team_a["id"] == team_b["id"]:
            st.warning("Pick two different teams to compare.")

        st.divider()
        show_left, show_right = st.columns(2)
        render_team(conn, show_left, team_a)
        render_team(conn, show_right, team_b)
    finally:
        conn.close()


main()
