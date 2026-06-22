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

- Team identity and roster, current and regional ranking, overall record, recent
  matches, current form and streak (with a form sparkline), and earnings.
- Per-map win rates with attack and defense side splits, pistol-round win rate,
  and opening-duel win rates at team and player level.
- Aggregated per-player statistics (rating, ACS, K/D, KAST, ADR, per-round
  figures, headshot percentage) with agent pools and per-agent performance.
- A player-versus-player view that aligns the two rosters by inferred role.
- Veto and map-pool reconstruction: the likely picks, probable decider, and
  likely bans for the matchup, with each team's win rate on those maps.
- Data-honesty aids: sample sizes with small-sample flags, a roster timeline
  with a current-five filter, a LAN versus online toggle, a stale-data flag, and
  a rough patch-era banner.
- Reasoning aids: common opponents, a free-text notes field per matchup, and a
  personal matchup log you can resolve and review later.

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

## Development

Run the tests with the VALTrack environment:

```
.venv/Scripts/python -m pytest tests/
```

The aggregation logic (side splits, pistol and opening-duel rates, player
aggregates, veto reconstruction) is unit tested, since an error there would
quietly corrupt the comparison. The UI is verified by hand.
