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

from valtrack import db, queries, stats, veto
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
    map_rows = queries.team_map_results(conn, team["id"], window)
    round_rows = queries.team_rounds(conn, team["id"], window)
    table = stats.per_map_splits(map_rows, round_rows, team["name"])
    if not table:
        st.caption(
            "No per-map detail stored in this range. Run the detail harvest "
            "(python harvest.py --pass details) to populate it."
        )
        return
    rows = []
    for m in table:
        decided = m["won"] + m["lost"]
        rows.append({
            "Map": m["map_name"],
            "Maps": f"{m['won']}-{m['lost']}",
            "Map win%": pct(m["map_winrate"]) if decided else "-",
            "ATK win%": pct(m["atk_winrate"]),
            "ATK rounds": m["atk_total"],
            "DEF win%": pct(m["def_winrate"]),
            "DEF rounds": m["def_total"],
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True)
    st.caption(
        "Map win% is over decided maps. Side win rates are over rounds played "
        "on that side. Round and map counts are shown so a small sample is "
        "visible."
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
    round_rows = queries.team_rounds(conn, team["id"], window)
    p = stats.pistol_winrate(round_rows, team["name"])
    if p["total"] == 0:
        st.caption(
            "No per-map detail stored in this range. Run the detail harvest "
            "(python harvest.py --pass details) to populate it."
        )
        return
    overall, atk, defense = st.columns(3)
    overall.metric("Pistol win%", pct(p["winrate"]), help=f"{p['won']} of {p['total']}")
    atk.metric(
        "ATK pistol%", pct(p["atk_winrate"]), help=f"{p['atk_won']} of {p['atk_total']}"
    )
    defense.metric(
        "DEF pistol%", pct(p["def_winrate"]), help=f"{p['def_won']} of {p['def_total']}"
    )
    st.caption(
        f"Pistol win rate over {p['total']} pistol rounds (round 1 and round 13 "
        "of each map). Eco and anti-eco conversion are not shown: the data source "
        "returns broken per-map economy, so those figures are deferred until it is "
        "fixed."
    )


def render_opening(conn, team, window):
    """Team and per-player opening-duel win rates with attack and defense splits.

    Computed from the per-map first-kill and first-death counts in the stored
    detail, so it only has figures where per-match detail has been harvested. The
    counts are per-map totals, not per-round events, so the split is over the
    opening duels taken on each side rather than a round-by-round timeline. The
    duel counts are shown so a thin sample stays visible.
    """
    st.divider()
    st.subheader("Opening duels")
    rows = queries.team_player_opening(conn, team["id"], window)
    o = stats.opening_duels(rows, team["name"])
    if o["duels"] == 0:
        st.caption(
            "No per-map detail stored in this range. Run the detail harvest "
            "(python harvest.py --pass details) to populate it."
        )
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
            "Player": p["player_name"],
            "FK": p["fk"],
            "FD": p["fd"],
            "Duels": p["duels"],
            "Win%": pct(p["winrate"]),
            "ATK%": pct(p["atk_winrate"]),
            "DEF%": pct(p["def_winrate"]),
        })
    st.dataframe(pd.DataFrame(player_rows), hide_index=True)
    st.caption(
        "Opening-duel win rate is first kills over opening duels (first kills "
        "plus first deaths). The attack and defense splits are per-side totals, "
        "not a round-by-round timeline, since the source stores only per-map "
        "first-kill and first-death counts. Duel counts are shown so a small "
        "sample is visible."
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


def render_player_stats(conn, team, window):
    """Per-player aggregated statistics for the window, with a per-agent view.

    Computed from the stored per-map player lines, so it only has figures where
    per-match detail has been harvested. The rate stats (rating, ACS, ADR, KAST,
    headshot percentage) are round-weighted across the player's maps; K/D and the
    per-round figures are summed then divided. Maps and rounds are shown as the
    sample size. Clutch statistics are not shown: the data source does not expose
    them, so they are left out rather than guessed.
    """
    st.divider()
    st.subheader("Player statistics")
    rows = queries.team_player_stats(conn, team["id"], window)
    players = stats.player_aggregates(rows, team["name"])
    if not players:
        st.caption(
            "No per-map detail stored in this range. Run the detail harvest "
            "(python harvest.py --pass details) to populate it."
        )
        return
    table = []
    for p in players:
        table.append({
            "Player": p["player_name"],
            "Rating": num2(p["rating"]),
            "ACS": num1(p["acs"]),
            "K/D": num2(p["kd"]),
            "KAST": pct100(p["kast"]),
            "ADR": num1(p["adr"]),
            "KPR": num2(p["kpr"]),
            "APR": num2(p["apr"]),
            "HS%": pct100(p["hs_pct"]),
            "FKPR": num2(p["fk_per_round"]),
            "FDPR": num2(p["fd_per_round"]),
            "Maps": p["maps"],
            "Rounds": p["rounds"],
        })
    st.dataframe(pd.DataFrame(table), hide_index=True)
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
                    "Rating": num2(a["rating"]),
                    "ACS": num1(a["acs"]),
                    "K/D": num2(a["kd"]),
                })
            st.dataframe(pd.DataFrame(agent_rows), hide_index=True)
    st.caption(
        "Rating, ACS, ADR, KAST, and HS% are round-weighted averages across the "
        "player's maps; K/D, KPR, APR, and the first-kill and first-death per-round "
        "rates are totals over the rounds played. HS% is round-weighted as an "
        "approximation, since the source stores only the per-map percentage. Maps "
        "and rounds are shown so a small sample is visible. Clutch statistics are "
        "not available from the data source."
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
        "name": player["player_name"],
        "rating": num2(player["rating"]),
        "acs": num1(player["acs"]),
        "kd": num2(player["kd"]),
        "kast": pct100(player["kast"]),
        "open": pct(player["open_winrate"]),
    }


def _team_map_splits(conn, team, window):
    """Per-map splits for a team keyed by map name, for the win-rate payoff."""
    table = stats.per_map_splits(
        queries.team_map_results(conn, team["id"], window),
        queries.team_rounds(conn, team["id"], window),
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
    a_tend = veto.team_tendencies(
        queries.team_vetos(conn, team_a["id"], window), team_a["tag"]
    )
    b_tend = veto.team_tendencies(
        queries.team_vetos(conn, team_b["id"], window), team_b["tag"]
    )
    pool = veto.active_pool(a_tend, b_tend)
    if not pool:
        st.caption(
            "No veto data stored in this range for these teams. Run the detail "
            "harvest (python harvest.py --pass details) to populate it."
        )
        st.divider()
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

    played = set(rec["likely_played"])
    tags = {}
    if rec["a_pick"]:
        tags[rec["a_pick"]] = f"{team_a['tag'] or 'A'} pick"
    if rec["b_pick"]:
        tags.setdefault(rec["b_pick"], f"{team_b['tag'] or 'B'} pick")
    if rec["decider"]:
        tags.setdefault(rec["decider"], "decider")
    pool_rows = []
    for r in rec["rows"]:
        pool_rows.append({
            "Map": r["map"],
            "Likely": tags.get(r["map"], "ban"),
            f"{team_a['tag'] or 'A'} pick%": pct(r["a_pick_rate"]),
            f"{team_a['tag'] or 'A'} ban%": pct(r["a_ban_rate"]),
            f"{team_b['tag'] or 'B'} pick%": pct(r["b_pick_rate"]),
            f"{team_b['tag'] or 'B'} ban%": pct(r["b_ban_rate"]),
            "Play likelihood": f"{r['play_score']:+.2f}",
        })
    st.dataframe(pd.DataFrame(pool_rows), hide_index=True)

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
        "maps, it does not predict the match winner. Pick and ban rates are over "
        "the matches each map was in the pool."
    )
    st.divider()


def render_player_vs_player(conn, team_a, team_b, window):
    """Align the two rosters by inferred role and compare player against player.

    Each player's role is inferred from their agent usage (see valtrack.agents),
    then the two teams are paired within each role. The headline figures, rating,
    ACS, K/D, KAST, and opening-duel win rate, are shown mirrored so like lines up
    against like. Built on the same windowed per-map detail as the per-team
    sections, so it only has figures where detail has been harvested.
    """
    st.divider()
    st.header("Player versus player")
    a_players = stats.player_aggregates(
        queries.team_player_stats(conn, team_a["id"], window), team_a["name"]
    )
    b_players = stats.player_aggregates(
        queries.team_player_stats(conn, team_b["id"], window), team_b["name"]
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
        "position within a role; an empty cell means one team had fewer players "
        "in that role in this range. Opening-duel win rate is first kills over "
        "opening duels. Figures rest on the windowed per-map detail, so a small "
        "sample shows in the per-team player tables above."
    )


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
        render_map_splits(conn, team, window)
        render_pistol(conn, team, window)
        render_opening(conn, team, window)
        render_player_stats(conn, team, window)
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
        render_veto_reconstruction(conn, team_a, team_b, window)

        show_left, show_right = st.columns(2)
        render_team(conn, show_left, team_a, window)
        render_team(conn, show_right, team_b, window)

        render_player_vs_player(conn, team_a, team_b, window)
    finally:
        conn.close()


main()
