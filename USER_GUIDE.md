# VALTrack User Guide

A plain-language guide to using VALTrack, the two-team VCT comparison tool. No
coding needed. If you still need to install it, follow the setup steps in the
README first; this guide picks up once the app is open in your browser.

## The one thing to know first

VALTrack never tells you which team is better. It shows each team's numbers side
by side and the gap between them, and leaves the judgment to you. There is no
overall score, no power ranking, and no "team A wins" verdict anywhere. Everything
below is about reading the numbers and the context around them.

## Opening the app

The easiest way is the launcher, which starts the data source and the app
together and opens it at http://localhost:8501:

```
.venv/Scripts/python launch.py        # Windows
.venv/bin/python launch.py            # macOS or Linux
```

If you only want to look at data you have already collected (no fetching), you can
skip the launcher and run the app on its own:

```
.venv/Scripts/streamlit run app.py
```

Everything runs on your own machine. Nothing you do is sent anywhere.

## Step 1: pick your two teams

All the controls live in the **sidebar** on the left.

- Under **Teams**, choose **Team A** and **Team B**.
- If the dropdowns feel crowded, narrow them with the **Leagues** filter (for
  example, show only Americas and EMEA).
- The small toolbar under the pickers:
  - **Swap** flips which team sits on the left.
  - **Save** stores this matchup so you can reload it in one click later (it turns
    into **Saved**, click again to unsave).
  - **Reset** clears your filters and toggles back to the defaults.

## Step 2: set the date range

Under **Date range**, every figure that depends on time (record, form, the per-map
splits, and so on) recalculates for the span you pick:

- **All time** is the default.
- Quick presets cover the last 3 or 6 months, year to date, the current split
  (approximate), and "since last roster change", which ties the stats to the
  lineup that will actually play.
- **Custom range** lets you set exact start and end dates.

A few figures do not move with the date range: world and regional ranking, the VLR
rating, and earnings are VLR's current snapshot, and the app says so where they
appear.

## Step 3: read the comparison

The comparison is split into four pages, switched with the tab bar near the top.
At the top of every page there are two buttons worth knowing:

- **How to read this**: a short reminder of the conventions (the gap, the thin
  sample marker, what a dash means).
- **Glossary**: a plain definition of every statistic and how VALTrack computes it.

### Compare (the default page)

The direct two-team view.

- It opens with the **headline gap bars**, a quick visual of where the two teams
  separate on win rate, pistol rate, and opening-duel rate.
- Then the **aligned core figures**, one row per statistic with each team's value
  and the gap between them.
- A **View** toggle switches the layout:
  - **Aligned** (default): one shared table per statistic, so the two teams line up
    row by row.
  - **Side by side**: each team in its own column, with every section in a panel you
    can open or close.
- At the very bottom, **League reference points** is an optional panel (off by
  default) that shows where each team sits against the whole franchise field on a
  statistic, so you can tell whether a number is actually any good. It is off by
  default because it reads every team's data.

### Match prep

A matchup-first briefing for reading a game that is about to happen.

- Each team's **card** with its record and a recent-form sparkline.
- **Things to watch**: the honesty flags that apply right now (stale data, thin
  detail coverage, an old or empty head-to-head, a tagged LAN match, and so on).
- The **map duel board**: each likely map shown as a side duel, one team attacking
  against the other defending.
- **Biggest differences**: the gaps between the two teams sorted largest first, so
  what separates them sits on top.
- It defaults to a recent window (since all-time data mixes old rosters and metas),
  with a toggle to use the date range from the sidebar instead.
- An expander can **load the upcoming franchise matches** from the live feed and
  drop one straight into the comparison.

### Maps and matchup

Everything about the map pool and the direct matchup.

- **Veto and map-pool reconstruction**: the likely picks, the probable decider, and
  the likely bans for these two teams, with each team's win rate on those maps.
  There is also a **manual what-if** mode where you drive the bans and picks
  yourself and watch the win rates respond.
- **Map-by-map breakdown**: for each likely map, both teams' record, the side
  duels, how fresh the sample is, the most-run agent composition, and the
  head-to-head on that map.
- **Head-to-head**: the direct record, broken down to the maps the two teams
  actually played and annotated with how old each meeting is.
- **Player versus player**: the two rosters lined up by role.
- **Common opponents**: teams both sides have faced, useful across regions where
  they rarely meet directly.

### My notes

Your own private workspace, stored locally.

- **Saved matchups**: the pairs you saved with the star, reloadable in one click.
- **Notes**: a free-text box per matchup for things the data does not capture.
- **Matchup log**: see the section below.

## Reading the tables

A few cues appear everywhere. The "How to read this" popover repeats these in the
app:

- **The gap** is Team A minus Team B on one statistic, and nothing more. It is
  never a tally across categories.
- **(!)** marks a thin sample (few matches or rounds), so read that number with
  care.
- **A dash (-)** means there is no data, which is not the same as a zero.
- **A faded, italic number** is resting on a thin sample.
- **A shaded cell** marks the higher team on that one row. It points out a single
  difference, it does not crown an overall winner.
- **(likely)** next to a map name means it is one of the maps these two teams are
  likely to play.
- Sample sizes (matches, rounds, maps) are shown next to figures so you can judge
  how much to trust them.

## More filters (in the sidebar)

The **More filters** section starts collapsed. Open it for:

- **Event type**: All, International LAN, or Online. LAN versus online is a
  best-effort guess from the event name.
- **Stage**: All, Group / swiss, or Playoff / elimination, a best-effort guess from
  the bracket label.
- **Current five only**: narrows the player statistics to each team's current five
  players. Team and round figures still cover everyone who played.
- **Current map pool only**: hides maps that have left the current rotation.

Under **Data**, an **Appearance** expander has a colorblind-safe color option that
swaps the green and red cues for blue and orange.

## The matchup log and your confidence

On the **My notes** page, the matchup log lets you record and later score your own
reads. It never scores a team, only your judgment.

1. Add an entry with a pre-match note, a **Confidence** level (very low to very
   high), and **who you lean toward** (Team A, Team B, or No lean).
2. After the match, record who actually won.
3. The **calibration** readout then groups your resolved, leaned calls by
   confidence and shows your hit rate at each level, so you can see whether your
   high-confidence calls actually come in more often than your low-confidence ones.

A call only counts toward calibration once it has both a lean and a recorded
outcome, and the readout is noisy until you have logged a good number of calls.

## Keeping your data fresh

- The **Refresh data** button (sidebar, under **Data**) pulls only the matches
  newer than your last update. It never runs the slow full harvest.
- A line under the button reports when the data was last updated. A warning appears
  if the data is getting old, or if the last refresh failed, which usually means
  the data source is not running.
- A **patch-era badge** near the top marks the rough game-version span your data
  covers. A wide date range mixes different maps, agents, and metas, so read across
  it with care.

## Where your settings and notes are saved

- Your notes, matchup log, saved matchups, and display preferences are stored in a
  local database file on your machine. Nothing leaves your computer.
- Your current two teams, date range, and toggles are also kept in the page URL, so
  a refresh or a bookmark reopens the same comparison.
- If you ever delete or rebuild the database to refresh data, back it up first: the
  scraped stats can be re-fetched, but your own notes and log cannot.
