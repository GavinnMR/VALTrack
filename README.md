# VALTrack

A local data comparison tool for VCT (Valorant Champions Tour) franchise teams.
Pick any two of the franchise teams from the four international leagues
(Americas, EMEA, Pacific, China) and view their statistics aligned side by side,
filtered to a custom date range. It is built for direct two-team comparison, in
the spirit of stat sites like VLR.gg but focused on one matchup at a time.

VALTrack presents data and the gaps between two teams. It does not compute an
overall score, a rating, or a "winner" across categories. It shows the
individual statistics and leaves the interpretation to you, and it surfaces the
context that says when a number is thin or stale so the data does not mislead.

## What it shows

Two views of the same matchup, switchable at the top of the comparison:

- **Side by side**: each team in its own column, with a selectable set of
  sections so you can focus on maps, players, or veto without scrolling past the
  rest.
- **Aligned**: one shared table per statistic, with each team's value and the gap
  between them, and maps in a single shared order so the rows line up.

Either way, an at-a-glance strip up top collects the headline numbers (win rate,
pistol rate, opening-duel rate, team rating) with the gap, and a per-team summary
states how many matches and what date span back the figures, plus how many of
them carry per-match detail.

The detailed sections cover:

- Team identity and roster, current and regional ranking, overall record, recent
  matches (results colored win or loss), current form and streak (with a
  sparkline), and earnings.
- Per-map win rates with attack and defense side splits, pistol-round win rate,
  the win rate of the round right after a pistol (split by whether the pistol was
  won or lost), eco and buy-type conversion (round win rate by buy type, from full
  save through full buy), how rounds are won (elimination, defuse, time, or spike,
  split by attack and defense side), and opening-duel win rates at team and player
  level.
- A round-margin profile alongside the record: the close-game record (maps decided
  by two rounds or fewer), the overtime record, and the average winning and losing
  margin, so two teams with the same win rate but opposite characters separate.
- The same statistics split by opponent strength tier (top 10, 11 to 30, and the
  rest), so "beats weak teams, struggles against the elite" shows at a glance.
- Aggregated per-player statistics (rating, ACS, K/D, KAST, ADR, per-round
  figures, headshot percentage) with agent pools and per-agent performance, plus
  the per-map spread of each player's rating (a steady performer and a
  feast-or-famine one read differently), plus the same player lines split by map.
- Per-player clutches won by 1vX depth, multikill counts (2K through 5K), and
  plant and defuse counts, all reported as counts rather than rolled into a
  rating (VLR exposes clutches won by situation, not attempts, so no clutch win
  rate is invented).
- Series pressure as separate figures: win rate on deciding maps, series win rate
  when a match reaches a decider, and comebacks after losing the opening map.
- Recent form (a rolling recent window) beside the selected window with the gap,
  so a team trending up or down reads as a number.
- A player-versus-player view that aligns the two rosters by inferred role.
- Veto and map-pool reconstruction: the likely picks, probable decider, and
  likely bans for the matchup, with each team's win rate on those maps and how
  recent that sample is, a manual what-if mode that lets you drive the bans and
  picks yourself, and a map-pool overlap lens that marks where the two teams'
  strong maps collide or diverge.
- A map-by-map breakdown that gathers, per likely-played map, both teams' record
  and win rate, the cross-side duels (each team attacking against the other
  defending), each team's sample recency, the most-run composition, and the
  head-to-head on that map, so a veto can be reasoned one map at a time without
  cross-referencing several sections.
- A contextualized head-to-head, broken to the maps the two teams actually played
  against each other and how old the meetings are, with each past meeting
  annotated with its date, LAN versus online, the maps and scores, the lineup each
  side fielded, and how much of that lineup is on the current five.
- League reference points that show where each team sits against the whole
  franchise field on a chosen statistic (a percentile and the field's low, median,
  and high), so you can tell whether a number is any good, never rolled into a
  ranking.
- Reasoning aids: common opponents, a free-text notes field per matchup, and a
  personal matchup log you can edit, delete, resolve with a structured winner, and
  review later, plus a calibration readout of how often your own confident calls
  came in.

All tables sort numerically by column and show data bars where they help, the
stat abbreviations carry tooltips and an inline glossary that describes how each
figure is actually computed, and the data-honesty aids stay throughout: sample
sizes with small-sample flags (which also fade the unreliable cells in the aligned
tables), a detail-coverage indicator, lineup continuity and a roster timeline with
a current-five filter, rest and recent-load context, a LAN versus online toggle, a
group versus playoff stage filter, a stale-data flag, and a rough patch-era banner.

The two picks, the date range (all time, quick presets including a since-last-
roster-change window, or a custom range), and the toggles are kept in the URL, so
a comparison survives a refresh and can be bookmarked, and a one-click swap flips
which team sits on the left. When the data API is running, the upcoming match
between the two teams can be auto-detected to tag the comparison.

## How it works

There are three pieces:

1. **vlrggapi**, an unofficial REST API over VLR.gg, run locally as a plain
   Python process. It is only needed when fetching data, not for viewing.
2. **An ingestion script** that pulls matches from the local API, computes the
   team-level statistics VLR does not provide pre-summed, and writes them to a
   local SQLite database.
3. **The Streamlit app**, which reads from SQLite and renders the comparison. It
   does not call the API during normal viewing.

Because all data comes from scraping VLR.gg, the local database insulates the app
from upstream changes: viewing always works on what is stored, and only fetching
new data depends on the API.

## Prerequisites

- Python 3.11 or newer (includes pip, venv, and sqlite3).
- Git.

## Setup

The data source and the app live in separate virtual environments so their
dependencies do not collide.

### 1. Clone this repository

```
git clone https://github.com/GavinnMR/VALTrack.git
cd VALTrack
```

### 2. Set up the VALTrack environment

```
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt      # Windows
.venv/bin/python -m pip install -r requirements.txt          # macOS or Linux
```

### 3. Set up the data source (vlrggapi)

Clone vlrggapi into the repo root as `vlrggapi/` (it is ignored by git and is not
part of this repository), then give it its own environment:

```
git clone https://github.com/axsddlr/vlrggapi.git
cd vlrggapi
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt      # Windows
cd ..
```

vlrggapi serves at http://127.0.0.1:3001. If the harvest below starts returning
HTTP 429 (rate limited), raise the per-endpoint limits in
`vlrggapi/api/utils/rate_limiter.py` to match a self-hosted instance; the client
already paces itself politely toward VLR.gg.

### Clone patches

The vlrggapi clone is gitignored and not part of this repository, so a few small
edits to it have to be re-applied after any fresh clone. They are all additive and
contained to `vlrggapi/api/scrapers/match_detail.py` (plus the rate limit above),
and each is marked in that file with a `VALTrack clone patch` comment:

- per-side first kills and deaths (the attack/defense opening-duel split),
- the round win-condition icon (elimination, defuse, time, or spike),
- which team picked each map (the pick label only reads "PICK"; the team is in its
  CSS class),
- per-map economy: select each map's own economy block by game id instead of the
  first map's table for every map,
- strip the team tag from the performance-tab player name so it matches the plain
  alias used elsewhere.

Without these the app still runs, but the affected figures (opening duels,
economy, round win conditions, and the veto pick cross-check) stay empty.

The exact diff is kept in `vlrggapi-patches/clone-patches.diff`, so after a fresh
clone you can apply all of them at once from inside the `vlrggapi/` folder:

```
git apply ../vlrggapi-patches/clone-patches.diff
```

See `vlrggapi-patches/README.md` for the base commit and what to do if upstream has
moved on.

## First-time data harvest

This is a one-time load, run from the terminal, not the app. It has two passes.
Start vlrggapi first, in its own terminal, from inside the `vlrggapi` folder:

```
.venv/Scripts/python main.py        # Windows
```

Then, from the repo root with the VALTrack environment:

```
.venv/Scripts/python harvest.py --pass cheap --scope full
.venv/Scripts/python harvest.py --pass details --scope full
```

The cheap pass loads teams, rankings, rosters, and match histories and is
reasonably quick. The detail pass pulls per-match data (rounds, player stats,
vetos) for every match and is the slow one, potentially several hours. Both are
safe to stop and re-run: they resume where they left off and never reload what is
already stored. Run Python with `-u` if you want the progress lines to stream.

If a database was detailed before the per-map economy and series-performance
tables existed, those rich sections (economy, clutches, multikills, plants and
defuses, round win conditions) stay empty on the older matches. A bounded
re-detail backfills them without re-fetching the whole table. Keep it to a recent
window, since a scout does not need years-old economy:

```
.venv/Scripts/python harvest.py --pass details --redetail --since 2025-01-01
```

This re-fetches only the matches in range that are missing those tables (plus any
brand-new match), and is safe to stop and re-run like the other passes. Going
forward, the in-app refresh fills the rich tables for new matches automatically,
as long as the running vlrggapi has the clone patches applied.

## Running the app

The simplest way starts the data source and the app together:

```
.venv/Scripts/python launch.py        # Windows
.venv/bin/python launch.py            # macOS or Linux
```

This brings up vlrggapi in the background, waits for it, then opens the app at
http://localhost:8501. Closing the app shuts the API down again.

To run just the app on already-stored data (no new fetching), skip the launcher:

```
.venv/Scripts/streamlit run app.py
```

## Keeping data fresh

Inside the app, the **Refresh data** button runs an incremental update: it pulls
only matches newer than the last update and details the newest of them. It never
runs the full all-time harvest. A banner reports data freshness, with one state
when the stored data is getting old and another when the last refresh attempt
failed, which usually means the API is not running.
