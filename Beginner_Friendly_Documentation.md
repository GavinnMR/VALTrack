# VALTrack: A Beginner's Guide

This guide explains how VALTrack is built, written for someone who is still learning to program. It defines technical terms as it goes and focuses on the "why" behind each part, not just the "what". Everything here is based on the actual code in this repository.

If you want the dense, reference-style version instead, see `Technical_Documentation.md`. This guide is the friendly walkthrough.

---

## What This App Does (in plain words)

VALTrack is a tool for comparing two professional Valorant teams.

Valorant has a pro league called the VCT (Valorant Champions Tour) with franchise teams across four regions (Americas, EMEA, Pacific, China). VALTrack lets you pick any two of those teams and see their statistics laid out next to each other: how often they win, how good their players are, which maps they are strong on, and so on. You can also filter to a date range, for example "only the last three months", and every number recalculates for that window.

There is one rule that shapes the entire app: **VALTrack never tells you which team is better.** It will not give a combined score, a power ranking, or a "team A wins" verdict. It shows you the individual numbers and the difference between them, and lets you decide what matters. This is a deliberate design choice, repeated all over the code, and it is the single most important thing to understand before changing anything.

The app runs on your own computer for one person (you). It is not a website for the public.

---

## The Big Picture

### A few terms you will need

Let us define some words up front so the rest makes sense.

- **Database**: a structured place to store data on disk. VALTrack uses **SQLite**, which is a database that lives in a single file (`valtrack.db`). No separate server needed.
- **API** (Application Programming Interface): a way for one program to ask another program for data over the network. VALTrack gets its Valorant data from an API called `vlrggapi`.
- **Scraping**: pulling data out of web pages that were meant for humans to read. `vlrggapi` scrapes a stats website called VLR.gg.
- **Streamlit**: a Python library that turns a plain Python script into an interactive web app in your browser. You write normal Python, and Streamlit draws the buttons, tables, and charts.
- **Query**: a request to the database for some data ("give me all of Sentinels' matches since January").
- **Aggregation**: combining many small rows of data into summary numbers (turning 400 individual rounds into one "attack win rate").

### The three parts of the system

VALTrack is split into three pieces that each have one job. Picture an assembly line.

```
   1. DATA SOURCE              2. INGESTION                3. THE APP
   (vlrggapi)        ->        (harvest scripts)    ->     (Streamlit)
   scrapes VLR.gg             fetches + computes          reads + displays
   gives raw JSON            saves to SQLite file        the comparison
```

1. **The data source (`vlrggapi`)**. A separate program that scrapes VLR.gg and serves the data at a local web address (`http://127.0.0.1:3001`). It is its own project, not written by VALTrack, and you install it separately. It is only needed when fetching fresh data.

2. **The ingestion step**. VALTrack's own code that asks the data source for matches, calculates the team-level statistics, and saves everything into the SQLite file. ("Ingestion" just means "taking data in and storing it.")

3. **The app**. The Streamlit program you actually look at. It reads from the SQLite file and draws the comparison. Crucially, **the app never talks to the data source during normal use.** It only reads the saved database.

### Why split it into three parts?

This is a really common and useful pattern, so it is worth understanding.

Because all the data ultimately comes from scraping a website, it is fragile. If VLR.gg changes its page layout, the scraper can break. By saving everything into a local database, the app is protected: looking at your teams always works on the data you already saved, and only the "fetch new data" step depends on the scraper being healthy. Separating "get the data" from "show the data" means a problem in one does not take down the other.

There is also a speed reason. Fetching and calculating all the stats is slow (potentially hours for a full load). You do not want to redo that every time you open the app. So you do it once, save the results, and the app just reads them quickly.

---

## Folder and File Breakdown

Here is what each major part does. The heart of the project is the `valtrack/` folder, which is a **package** (a folder of related Python files that work together).

### Top-level files (the entry points)

These are the files you actually run.

- **`app.py`** The Streamlit app. This is the big one (around 4,000 lines). It contains all the on-screen layout: the team pickers, the pages, the tables, the charts. It reads data through the helper modules and draws it. It does not contain any database queries itself; it asks `queries.py` for data. The overall colors, fonts, and rounded corners are set separately in `.streamlit/config.toml` (a small theme file), not in `app.py`.
- **`harvest.py`** The command-line tool you run in a terminal to fetch data. It has two modes ("passes") explained later. You run this once at the start to fill the database.
- **`launch.py`** A convenience launcher. It starts the data source in the background, waits for it to be ready, then opens the app. When you close the app, it shuts the data source down. This saves you from juggling two terminals.
- **`schema.sql`** The blueprint for the database. It is a text file of SQL commands that create every table. ("SQL" is the language databases speak.)
- **`requirements.txt`** / **`requirements-dev.txt`** The list of Python libraries the project needs (streamlit, pandas, requests, plotly, and for development, pytest and ruff).

### The `valtrack/` package (the brains)

It helps to group these files by their job.

**Getting data in (fetching and saving):**

- **`api_client.py`** Talks to the data source. It wraps the web requests in a class called `VlrClient` with one method per thing it can ask for (team profile, rankings, match details, and so on). It also waits politely between requests so it does not hammer VLR.gg, and retries if a request fails.
- **`ingest.py`** The ingestion engine. It loops over every franchise team, fetches their data, and saves it. It is careful to save progress as it goes, so if it crashes halfway through, the teams it already finished are kept.
- **`match_detail.py`** Handles the detailed, per-match data (the round-by-round stuff). It has one function that parses the raw data into clean rows, and another that saves those rows to the database.
- **`cleaning.py`** Small, simple helper functions that clean up messy data: fixing garbled text, turning a "1:2" score string into two numbers, parsing dates, and so on. These are "pure" functions (defined below in Gotchas), which makes them easy to test.
- **`franchise.py`** A hand-written list of the franchise teams and their VLR.gg ID numbers. There are 12 teams in each of the 4 leagues (48 total). This list exists because the data source has no "just the franchise teams" feed, so the team IDs are written down directly.

**Computing the statistics (the real engineering):**

- **`stats.py`** The biggest brain file. It takes plain rows of data and calculates every statistic: win rates, side splits, player ratings, opening duels, and more. It touches no database at all; it just does math on lists you hand it. This is the part most worth reading if you want to understand the project.
- **`veto.py`** The "map veto" feature. In Valorant, before a match teams take turns banning and picking maps. This file looks at each team's history and reconstructs which maps they would likely play against each other. The project calls this its flagship feature.
- **`agents.py`** A lookup table mapping each Valorant agent (character) to its role (duelist, controller, and so on). Used to guess a player's role from which agents they play.

**Reading data back out:**

- **`queries.py`** All the database read commands live here. Each function takes the open database connection and returns rows. The app uses these instead of writing its own SQL. This keeps all the database logic in one place.
- **`window.py`** Defines the date-range filter (and two related filters for event type and tournament stage). When you pick "last 3 months", this is what turns that choice into a piece of a database query.

**Supporting features:**

- **`journal.py`** Your personal data: the free-text notes you write, the matchup log where you record predictions, saved favorite matchups, and tags for upcoming matches. None of this is scraped; it is all your own input.
- **`db.py`** Database plumbing: opening the connection, creating tables, and gently upgrading an old database when new columns are added.
- **`eras.py`**, **`freshness.py`**, **`schedule.py`** Small focused helpers: a rough "what Valorant era is this date in" label, a "how many days old is the data" calculator, and a resolver that matches the live upcoming-match feed to franchise teams.

### The `tests/` folder

Automated tests that check the math is correct. There are over 200 of them, concentrated on `stats.py`, `queries.py`, and `veto.py`, because a bug in the calculations would quietly give you wrong numbers without any error. The visual app is checked by hand instead.

### Things that are not in the repository

- **`vlrggapi/`** The data source is a separate project you clone into the folder yourself. It is deliberately excluded from this repository.
- **`valtrack.db`** The database file. It is created when you run the harvest, so it is not stored in the repo (you generate your own).

---

## How the Main Features Work (step by step)

### Feature 1: Getting the data in (the harvest)

Before you can compare anything, you have to fill the database. This happens in two "passes", and understanding why there are two is key.

**The cheap pass** grabs the lightweight, list-level data: the teams, their rankings, their rosters, and a list of their matches (who played, the final score, the date). This is fast.

**The detail pass** grabs the heavy, per-match data: every round of every map, every player's stats per map, the economy, the map vetos. This requires one separate web request per match, so for a full history it can take hours.

*Why split them this way?* Two reasons. First, you often want the basic match list quickly without waiting hours for every round of every game. Second, the detail pass is the slow, fragile part, so keeping it separate means a hiccup there does not lose the cheap data you already have. The cheap pass also has to run first, because the detail pass needs the list of matches to know what to fetch detail for.

Both passes are **safe to stop and re-run**. If your computer sleeps or the connection drops, you just run the command again and it picks up where it left off. It does this by checking what is already saved and skipping it (more on this in Gotchas).

### Feature 2: Comparing two teams

This is the main screen. Here is the flow when you use it:

1. You pick Team A and Team B from dropdowns, and choose a date range.
2. The app asks `queries.py` for each team's matches, rounds, players, and so on, filtered to that date range.
3. It hands those rows to `stats.py`, which calculates the win rates, player ratings, side splits, and the rest.
4. It draws everything as tables and charts, showing Team A's value, Team B's value, and the gap between them.

*Why compute the stats fresh each time instead of saving them?* Because the date range is something you choose. A team's "attack win rate since March" is a different number from their all-time rate, and you can pick any window. There is no way to pre-calculate every possible date range, so the app recalculates the derived stats for whatever window you select. (The few things that do not depend on a window, like the team's current world ranking, are shown as a fixed snapshot and labeled as such.)

The comparison has two layouts you can switch between: **side by side** (each team in its own column) and **aligned** (one shared table per stat with the gap in a column). The app also lets you hide sections you do not care about, to keep the long page manageable.

### Feature 3: Veto and map-pool reconstruction

This is the feature the project is proudest of. In a real Valorant match, teams ban and pick maps before playing. VALTrack cannot know the future veto, but it can guess based on history.

Here is the idea in simple terms:

1. For each team, look at every past veto and count: how often did they ban each map? How often did they pick it?
2. From those tendencies, figure out which maps are "in the pool" right now (favoring maps that have appeared recently, so retired maps do not sneak in).
3. Simulate a likely veto: each team's most-picked map becomes their probable pick, and the leftover map becomes the likely "decider".
4. For those likely-played maps, show each team's win rate, including their attack-side and defense-side rates.

*Why reconstruct it from history instead of just listing all maps?* Because the useful question before a match is not "what are all the maps" but "what will these two specific teams probably play, and who is better there". The reconstruction narrows it down. And true to the project's rule, it never says who wins; it just surfaces the likely maps and the win rates, and lets you read them. There is even a manual mode where you drive the bans and picks yourself and watch the win rates update.

### Feature 4: The data-honesty features

A big theme in this app is not trusting a number blindly. Several features exist purely to warn you when a statistic might be misleading:

- **Sample-size flags.** A win rate based on 4 matches is shakier than one based on 40. The app marks thin samples with a small `(!)` flag and even fades them visually, so your eye is drawn to the trustworthy numbers.
- **Staleness warnings.** If a team has not played in over six weeks, its stats are frozen and possibly outdated, so the app says so. Separately, if you have not refreshed your data in a while, a banner nudges you.
- **Coverage notes.** Because the detail pass is slow, you might have basic data for a match but not the round-by-round detail. The app tells you how many matches actually have detail, so you know how complete a number is.
- **Roster and rotation context.** It shows when players joined, lets you filter to just the current five players, and marks maps that have left the current rotation.

*Why put this much effort into warnings?* The project's philosophy is that a confidently-shown wrong number is worse than an honest "we are not sure". Since the whole point is helping you reason about a matchup, hiding the weaknesses in the data would defeat the purpose.

### Feature 5: Notes and the matchup log

These are your own private features, stored locally.

- **Notes**: a free-text box per team pairing where you jot observations the data does not capture.
- **Matchup log**: you record a matchup, a confidence level, and which team you lean toward. Later you record who actually won. Over time, the app shows a "calibration" readout: when you said you were highly confident, how often were you right?

*Why is this separate from everything else?* Because it is the one place the app stores your input rather than scraped data. The code keeps it in its own file (`journal.py`) and its own database tables, and the ingestion step never touches it. The calibration scores your own judgment, never a team, so it still respects the no-winner rule.

### Feature 6: Refreshing the data

Inside the app there is a "Refresh data" button. It runs a small, fast update: it fetches only matches newer than your last update and grabs detail for the newest handful (capped, so the click stays quick). It deliberately never runs the full hours-long harvest. This is the everyday way to stay current without using the terminal.

---

## Data Flow Explained Visually

### The whole pipeline

```
                         (only during fetching)
   VLR.gg website
        |  scraped by
        v
   vlrggapi  ----HTTP/JSON---->  api_client.py (VlrClient)
                                       |
                                       |  raw, messy data
                                       v
                                  cleaning.py  +  match_detail.py
                                       |  (fix text, parse numbers,
                                       |   compute team-level stats)
                                       v
                                  ingest.py  --writes-->  [ valtrack.db ]
                                                              (SQLite file)
                                                                  ^
                                                                  |  reads only
   ============== from here down, no internet needed =============|=========
                                                                  |
   YOU (browser)  <--draws--  app.py  <--rows--  queries.py  <----+
        |                       ^
        |  click / pick         |  hands rows to
        v                       |
   Streamlit reruns  --------> stats.py / veto.py  (do the math)
```

The top half (fetching) runs occasionally. The bottom half (viewing) runs every time you interact, and it only ever reads the database.

### Walkthrough: what happens when you pick two teams

1. You choose Team A, Team B, and a date range in the browser.
2. Streamlit re-runs the whole `app.py` script from top to bottom (this is how Streamlit works; see Gotchas).
3. For each section of the page, the app calls a cached query function (named `cq_...`) which runs the right SQL in `queries.py` and returns rows.
4. Those rows go into a `stats.py` function that computes the numbers.
5. The app draws a table or chart, with Team A, Team B, and the gap.
6. Your selection is also written into the web address (URL), so you can bookmark or refresh and get the same comparison back.

### Walkthrough: what happens when you click "Refresh data"

1. The app calls the ingestion engine in "incremental" mode (only new things).
2. The cheap pass fetches any matches newer than your last update.
3. The detail pass fetches round-by-round detail for the newest few of those.
4. The app clears its saved (cached) results so the screen recalculates with the fresh data.
5. A success or failure message appears. If the data source was not running, it tells you the API may be down.

---

## Common Confusions and Gotchas

These are the tricky parts that trip people up. Read this section carefully if you plan to change the code.

### 1. Streamlit re-runs your entire script on every click

This is the most surprising thing about Streamlit for newcomers. There is no "onClick" handler running a small piece of code. Instead, **every time you touch any widget, Streamlit runs `app.py` again from the first line to the last.** The functions that draw widgets also read their current values.

Two consequences:

- **Caching matters.** If the script reran all the database queries every single time, the app would be slow. So the query functions are wrapped with `@st.cache_data`, which means "remember the result for these inputs and reuse it". When you refresh data, the code explicitly clears this cache so you see new results.
- **Remembering things between reruns** needs a special storage called `st.session_state` (a dictionary that survives reruns). The team you picked, your toggles, and so on live there.

### 2. The detail tables identify teams by name, not by ID

Most of the database links rows using ID numbers (a team's VLR.gg ID). But the detailed per-match tables (rounds, player stats) record teams by their **name** instead, because that is how the source data labels them.

So when the app wants a team's rounds, it first looks up that team's name from its ID, then searches the detail tables by name. There is a small helper for exactly this. The gotcha: if a team ever changed its stored name, old detail rows could fail to match. This is a known fragile spot.

### 3. Empty stat means "we do not know", not "zero"

All over the calculation code, when there is nothing to compute a rate from (zero rounds, zero matches), the function returns "nothing" (Python's `None`) instead of `0`. The screen then shows a dash.

*Why?* Because `0%` is a claim ("this team wins 0% of attack rounds") while a dash is honest ("we have no attack rounds to judge"). Showing a real zero where you actually have no data would mislead, which the project refuses to do. If you add a new stat, follow the same rule.

### 4. "No detail in this range" is normal, not a bug

Because the slow detail pass might not have reached every match, many sections will say something like "No per-map detail stored in this range" until you run the detail harvest. This is expected. The basic record and match list (cheap pass) will work long before the round-by-round sections fill in.

### 5. The data source needs manual patches

The `vlrggapi` data source is a separate project. To get a few of VALTrack's stats (the attack/defense opening-duel split, economy, round win conditions, and the veto pick cross-check), you have to apply some small hand edits ("clone patches") to the data source after you download it. The README lists them. Without the patches the app still runs fine; those specific sections just stay empty. If a feature is mysteriously blank even after harvesting, a missing patch is a likely cause.

### 6. "Pure functions" and why so much code avoids the database

You will see comments calling functions "pure". A **pure function** is one that only uses its inputs and returns a result, with no side effects: it does not read the database, call the internet, or change anything outside itself. Same input always gives the same output.

`stats.py`, `veto.py`, `cleaning.py`, and the filters in `window.py` are written this way on purpose. The benefit is that they are trivial to test: you can hand them a small made-up list and check the answer, no database required. That is why the test folder can have 200+ fast tests. When adding logic, prefer to put the calculation in a pure function and keep the database parts thin.

### 7. Some things are guessed, not stored (marked clearly in the code)

A few values are inferred rather than recorded as fact, and the app labels them as best-effort:

- **LAN vs online**: guessed from the event's name (international events are assumed to be on a LAN).
- **Tournament stage** (group play vs playoffs): guessed from the bracket label. Labels it cannot place are left out of both buckets rather than guessed wrong.
- **Player role**: guessed from which agents the player uses.
- **Series format** (best-of-3 vs best-of-5): figured out from the score, not stored.

If a number here looks slightly off, remember it is an educated guess by design.

### 8. Some database tables exist but are never filled

The blueprint (`schema.sql`) defines a few tables that are not actually used, kept so older databases do not need a risky rebuild. Do not assume they have data:

- `roster_changes` (the source's transactions feed was unreliable, so roster history is instead worked out from who actually played).
- `economy` (the round-by-round version; the source only gives a per-map summary, which is stored in `map_economy` instead).
- A couple of leftover "clutch" columns on the player-stats table.

*Uncertain:* the `match_format` column on the matches table appears to exist in the schema but is not written during ingestion; the format is computed when needed at read time instead. Treat that stored column as likely unused unless you confirm otherwise.

### 9. Pistol rounds are only rounds 1 and 13

In Valorant a "pistol round" is the first round of each half. The code treats only round 1 and round 13 as pistols and deliberately excludes overtime rounds. If you work on round logic, keep that in mind.

---

## How to Extend the App

Here is where to make changes for common additions. The project has a consistent shape, so following the existing pattern keeps things working.

### Before anything: respect the one rule

Whatever you add, **do not create a combined score, an overall rating across categories, or a "team A is better" output.** Show individual numbers and the gap between them. This rule is non-negotiable in this project and is the reason many functions return separate figures instead of one blended number.

### To add a new statistic

Follow the same path the existing stats take:

1. **Write the math** as a pure function in `stats.py` (or `veto.py` for veto-related logic). It should take plain rows and return a result, using `None` when there is nothing to compute. Add a test in `tests/` with a small made-up input and the expected answer.
2. **Add the database read** in `queries.py` if you need data not already fetched. Reuse the existing date/event/stage filter helper so your query respects the user's window.
3. **Show it** in `app.py`: add a small `render_...` function that calls a cached query wrapper, passes the rows to your `stats.py` function, and draws a table or chart. Include a sample-size flag and a friendly "no data yet" message for the empty case, like the other sections do.

### To add a new piece of stored data (a column or table)

1. Add it to `schema.sql` (the blueprint).
2. Register it in `db.py` so older databases get upgraded automatically. The code has a list of "columns added after launch" and helper functions that create missing tables. Keep changes additive (only add, do not remove), so existing databases keep working.
3. Write to it in the ingestion code (`ingest.py` or `match_detail.py`).

### To add a new filter (like the date range)

Model it on the existing filters in `window.py`. They are small immutable classes with a method that produces a piece of a SQL `WHERE` clause. You then thread the new filter through the query functions. One requirement: the filter must be "hashable" (usable as a dictionary key), which the existing frozen dataclasses already are, because the caching system uses it as part of the cache key.

### To change how the screen looks

All the visual layout is in `app.py`, organized into `render_...` functions, one per section, plus a `main()` function at the bottom that wires up the pages and calls them. Find the `render_...` function for the section you want to change. The four pages (Compare, Match prep, Maps and matchup, My notes) are assembled near the end of `main()` as a segmented control, and the global controls (team pickers, date range, filters) live in the sidebar. The overall colors and fonts come from the theme in `.streamlit/config.toml`, not from `app.py`.

### A good first exercise

Read `stats.py` from the top. It is the clearest window into what the app actually computes, it has no database or network code to distract you, and every function has a thorough comment explaining the idea and the edge cases. Pair it with `tests/test_stats.py` to see each function fed real inputs.
</content>
