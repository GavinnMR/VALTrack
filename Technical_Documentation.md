# VALTrack Technical Documentation

AI-optimized technical specification. Concise, implementation-focused. Reverse-engineered from the code, with uncertainty flagged where the implementation is ambiguous.

## System Overview

VALTrack is a local, single-user Streamlit application that compares exactly two VCT (Valorant Champions Tour) franchise teams side by side over a user-chosen date window. It harvests match data from a self-hosted `vlrggapi` instance (an unofficial REST scraper over VLR.gg) into a local SQLite database, then computes team-level statistics that the source does not pre-aggregate (side win rates, pistol rates, opening-duel rates, economy conversion, series pressure, veto tendencies) and renders them with the per-statistic gap between the two teams. A hard product constraint runs through the whole codebase: the app never produces a composite score, combined rating, category tally, or any single output that declares one team the winner. It presents aligned figures, the gaps between them, and the context (sample size, staleness, roster validity, map rotation) that says when a number is unreliable.

## Architecture Summary

Three runtime pieces, with a clean read/write split around SQLite.

1. **`vlrggapi`** (external, gitignored clone, self-hosted at `http://127.0.0.1:3001`). The data source. Needed only during ingestion, never during viewing. Requires manual clone patches (see Constraints) for several derived figures to populate.
2. **Ingestion** (terminal and in-app). Pulls from the API, parses, computes, writes SQLite. Entry points `harvest.py` (terminal) and the in-app Refresh button.
3. **Streamlit app** (`app.py`). Reads SQLite only. Holds no SQL of its own; all reads go through `valtrack/queries.py`, all computation through `valtrack/stats.py` and `valtrack/veto.py`.

### Module map (`valtrack/` package)

| Module | Role | Side effects |
|---|---|---|
| `db.py` | Connection, schema init, idempotent column/table migrations, `meta` key/value store, stage backfill | SQLite |
| `api_client.py` | `VlrClient`: thin HTTP wrapper over vlrggapi v2 endpoints, polite delay, retries, envelope unwrap | HTTP |
| `franchise.py` | Static seed of the 48 franchise teams (league, region, VLR id) and league-to-ranking-ladder map | none (pure data) |
| `agents.py` | Static agent-name to role table; `agent_role()` | none (pure data) |
| `cleaning.py` | Pure parsers: encoding repair, score/date/int/float parse, veto-string parse, side mapping, pistol test | none (pure) |
| `ingest.py` | Ingestion engine: cheap list pass and expensive detail pass, both scopes, both terminal entry funcs | SQLite + HTTP |
| `match_detail.py` | `parse_match_detail()` (pure) and `store_match_detail()` (idempotent write) for the per-match detail | SQLite (store only) |
| `window.py` | `DateWindow`, `EventFilter`, `StageFilter` filter dataclasses; `is_lan_event()`, `classify_stage()` | none (pure) |
| `queries.py` | All read-side SQL. Returns plain rows. Shares one windowing helper `_scope()` | SQLite (read) |
| `stats.py` | All pure aggregation and derivation. No DB access. The core engineering | none (pure) |
| `veto.py` | Veto tendency aggregation and map-pool reconstruction (flagship feature), pure | none (pure) |
| `journal.py` | Local user data: notes, matchup log, favorites, upcoming tag, UI preferences | SQLite (local tables) |
| `eras.py` | Coarse patch-era labels for the era banner, pure | none (pure) |
| `freshness.py` | `age_days()` for the staleness banner, pure | none (pure) |
| `schedule.py` | Resolve the live upcoming feed to franchise-vs-franchise pairs, pure | none (pure) |

### High-level data flow

```
vlrggapi (HTTP, v2 JSON)
  -> api_client.VlrClient            (unwrap envelope)
  -> cleaning.* / match_detail.parse (pure parse + encoding repair + team-level compute)
  -> SQLite tables                   (ingest.* / match_detail.store, idempotent upserts)
  -> queries.* (windowed SQL via _scope)   <-- app reads here, never the API
  -> stats.* / veto.* (pure aggregation over rows)
  -> app.py render (Streamlit widgets, Plotly, st.cache_data, session_state, URL params)
```

The app passes three frozen filter dataclasses (`DateWindow`, `EventFilter`, `StageFilter`) down through cached query wrappers into the SQL `WHERE` clause, so every windowed figure shares one definition of the active range and filters.

### Entry points

- `harvest.py` (terminal): `--pass cheap|details`, `--scope full|incremental`, `--limit`, `--redetail`, `--since`.
- `launch.py`: starts vlrggapi in the background, waits for `/version`, runs `streamlit run app.py`, tears the API down on exit. Viewing works even if the API fails to start.
- `app.py`: module-level `main()` call at import (Streamlit convention).
- Tests are run locally with `.venv/Scripts/python.exe -m pytest tests/`. There is no CI workflow, and `tests/` is gitignored (kept local, not in the public repo). Always scope the run to `tests/` so it does not collect the gitignored vlrggapi clone's own tests.

## Core Data Models

SQLite, single file `valtrack.db` at repo root (gitignored, regenerable). Schema in `schema.sql`. `db.connect()` enables `PRAGMA foreign_keys = ON` and `Row` factory. Primary keys use the VLR.gg ids directly, so the same entity pulled from two places collapses to one row.

### Identity and roster (cheap pass)

- **`teams`** (`id` PK = VLR team id): name, tag, logo, country, league (`americas|emea|pacific|china`), region (`na|eu|ap|cn`), `regional_rank`, `world_rank` (nullable), rating/peak_rating/streak/record/earnings/total_winnings (text snapshots), `last_played`, `fetched_at`.
- **`players`** (`id` PK = VLR player id): alias, real_name, country, avatar.
- **`rosters`** (PK `team_id, player_id`): current snapshot, `is_captain`, `is_staff` (unreliable, see Constraints), `role` (text). Replaced wholesale each harvest.
- **`roster_changes`**: declared but never populated (transactions endpoint unreliable). Roster history is derived from appearances instead.

### Series-level matches (cheap pass)

- **`matches`** (`match_id` PK = VLR match id): url, `event_round` (bracket label e.g. "LR2"), `event_name` (filled by detail pass), normalized `date` (YYYY-MM-DD), time, `team1_id/name/tag/logo`, `team2_id/name/tag/logo`, `team1_score`, `team2_score`, `winner_name`, `map_vetos_raw`, `details_fetched_at` (set once detail stored, even for a 0-map forfeit), `match_format` (`bo1|bo3|bo5`, currently inferred at read time not stored), `match_stage` (`group|playoff|unknown`, backfilled on app startup). Indexed on team1, team2, date.

### Per-match detail (expensive pass)

All keyed by `match_id`, FK to `matches`. **Important: these tables identify teams by name string (`team_name`, `team1_name`), not by team id.** Queries bridge via `_team_name(conn, team_id)`.

- **`map_results`**: one row per map. map_name, map_order, team1/2 name+score, per-team atk/def round totals, winner_name, `picked_by_name` (resolved from the patched pick label; NULL for the decider).
- **`map_player_stats`**: one row per player per map. player_id (resolved by alias, nullable), player_name, team_name, agent, rating/acs/kills/deaths/assists/kast/adr/hs_pct, first_kills/first_deaths and the four per-side splits (`first_kills_atk/def`, `first_deaths_atk/def`). `clutch_won/clutch_lost` columns exist but are legacy and unpopulated.
- **`match_player_perf`**: one row per player per match (series level, not per map). Multikills `mk_2k..mk_5k`, clutches by depth `clutch_1v1..clutch_1v5` (wins only, no attempts), plants, defuses.
- **`rounds`**: one row per decided round. map_name, round_number, `winner_side` (`atk|def`), winner_team (name), `win_type` (`elim|defuse|time|boom`, nullable for pre-patch detail), `is_pistol` (round 1 and 13).
- **`map_economy`**: per-map per-team aggregate buy-type table. buy_type (`eco|light|half|full`), played, won. Pistols excluded (handled by `rounds`).
- **`economy`**: declared but never populated (VLR exposes no round-by-round economy). Legacy.
- **`match_vetos`**: one row per veto action in order. seq, `team_token` (the VLR abbreviation in the veto string, NULL for "remains"), action (`ban|pick|remains`), map_name.

### Bookkeeping and local user data

- **`meta`** (key/value): `last_updated` (ISO), `last_status` (`ok|partial|failed`). Drives freshness banner.
- **`matchup_notes`** (PK `pair_key`): free-text note per team pair. `pair_key` = the two ids sorted and joined `"min-max"` (order independent).
- **`matchup_log`**: personal predictions. team a/b id+name, note, confidence, `predicted_side` (`a|b|null`), `outcome` (free text), `outcome_side` (`a|b|null`), created_at, resolved_at. Feeds calibration.
- **`matchup_favorites`** (PK `pair_key`): saved pairs for one-click reload.
- **`matchup_upcoming`** (PK `pair_key`): optional real-match tag (match_date, event_name, is_lan).

### Key relationships and subtleties

- `matches.team1_id`/`team2_id` are nullable during the team-by-team pass (an opponent not yet loaded). `link_match_teams()` backfills by name after the run. Non-franchise opponents stay NULL (not stored as teams).
- The same match appears in both teams' histories; the PK collapses it. The "queried team is team1" assumption means the last write wins the team1 slot, so read-side queries always check both slots (`team1_id = ? OR team2_id = ?`).
- Detail tables join back to `matches` only for the `date` (windowing), `event_name` (LAN filter), and `match_stage` (stage filter). Team identity inside detail tables is by name.
- Filter dataclasses are hashable (frozen), so they double as `st.cache_data` keys.

## Key Workflows

### 1. Cheap (list-level) harvest, `ingest.run_ingest(scope)`

Input: scope `full|incremental`. Per franchise team (from `franchise.iter_franchise_teams()`):
1. `team_profile(id)` -> identity, roster, rating, winnings.
2. `resolve_ranking()` searches the league's ranking ladders in priority order for a name match (regional rank; NULL for inactive orgs).
3. `upsert_team()` (upsert on id), `upsert_roster()` (delete + reinsert), `ingest_team_matches()` (page match history).
4. Commit per team (progress survives a later failure).
5. After all teams: `link_match_teams()` backfills opponent ids, stamp `meta`.

Paging stops when a page yields no new real match (`_is_real_match` rejects junk placeholder segments past the last real page) or, in incremental scope, at the first already-stored match (history is newest first).

Output: summary dict `{teams, unresolved, matches, errors}`. SQLite populated for teams/players/rosters/matches.

### 2. Detail (per-match) harvest, `ingest.run_detail_ingest(...)`

Input: scope, optional `limit`, `redetail`, `since`. Selects match ids needing work:
- normal: `matches_needing_detail()` (decided, `details_fetched_at IS NULL`).
- redetail: `matches_missing_analytics()` (detailed-but-missing `map_economy`, plus any undetailed), bounded by `since` to backfill the rich tables added after the first schema.

Per match: `match_detail(id)` -> `parse_match_detail()` (pure) -> `store_match_detail()` (idempotent: deletes the match's existing rich rows, then inserts; sets `details_fetched_at`). Commit per match. One API call per match, so this is the bulk of harvest time (hours for a full load).

Output: `{matches, maps, errors}`. SQLite populated for map_results, map_player_stats, match_player_perf, rounds, map_economy, match_vetos; `matches.event_name`/`map_vetos_raw` updated.

### 3. In-app incremental refresh, `app.run_incremental_refresh()`

Cheap incremental pass, then a detail incremental pass capped at `REFRESH_DETAIL_LIMIT = 100`. Clears `st.cache_data` so views recompute. The engine stamps `last_status`; on API failure it records `failed` and raises, surfaced as "API may be down". Never runs the full harvest.

### 4. Comparison render, `app.main()`

1. `_inject_css()` (small presentation-only style pass; the theme colors and fonts live in `.streamlit/config.toml`, not in `app.py`).
2. Open conn; `ensure_app_tables`, `ensure_analytics_tables`, `ensure_columns`, `backfill_match_stage` (self-heal an older DB on every launch).
3. `_apply_url_state()` then `_apply_saved_prefs()` seed widget defaults once per session (a shared URL wins; saved prefs fill the rest).
4. Sidebar holds every global control: league filter and the two team selectboxes, a swap/favorite/reset toolbar, the date range (`choose_window` -> `DateWindow`), a collapsed "More filters" expander (`EventFilter`/`StageFilter` radios, `five_only`, `pool_only`), and a "Data" section (`render_freshness` Refresh + staleness banner, palette). Widget keys are unchanged, so URL state and saved prefs still bind. Buttons carry a primary/secondary/tertiary hierarchy.
5. `_write_url_state()` mirrors the selection to the URL. Main-pane banner with the active-filter summary, a "How to read this" and a "Glossary" popover, a patch-era badge (`eras.patch_era_span`), a sticky header, and the likely pool / current rotation computed once.
6. Four pages via `st.segmented_control` (key `active_page`, default Compare; only the active page's body renders, so a filter change recomputes one page, not four): Compare (headline gap bars, the aligned core table or the Side-by-side per-team columns, league reference), Match prep (dashboard: cards, "Things to watch", duel board, biggest-difference gap view), Maps and matchup (veto, map breakdown, head-to-head, player-vs-player, common opponents), My notes (favorites, notes, matchup log). Section methodology lives in `help=` tooltips and a few "How this is computed" popovers rather than long captions.

Every section follows: cached query (`cq_*`) -> pure aggregation (`stats.*`/`veto.*`) -> render with sample-size flags and an "empty state" caption when no detail is stored in range.

### 5. Veto and map-pool reconstruction (`veto.py` + `render_veto_reconstruction`)

`team_tendencies(veto_rows, tag, name)` -> per-map ban/pick/appearance counts and rates (denominator is appearances, so a rotated-in map is judged only over matches it was available). Pick attribution: veto-string token resolves to the team tag, OR (fallback) `map_results.picked_by_name == team name`. `active_pool()` infers the pool from both teams' history, favoring maps seen within `recent_days` of the latest veto. `reconstruct()` mirrors a Bo3: `a_pick`/`b_pick` (each team's top pick-rate map), `decider` (highest play_score left), `likely_bans`, `likely_played`, where `play_score = (A pick% - A ban%) + (B pick% - B ban%)`. Output feeds win-rate-on-likely-maps tables, the duel board, the map breakdown, and a manual what-if simulator.

### 6. Player versus player (`stats.align_rosters` + `render_player_vs_player`)

Each player's role inferred from agent usage (`primary_role` over the agent pool), grouped by role, paired positionally within each role (`ROLE_ORDER`, shorter side pairs against None). Mirrored table of rating/ACS/K-D/KAST/opening-duel%.

## APIs / Interfaces

### External: vlrggapi v2 endpoints (consumed by `VlrClient`)

Envelope `{"status":"success","data":{...}}`, unwrapped to `data`.

| Method | Endpoint | Returns |
|---|---|---|
| `search(q)` | `/v2/search` | teams/players/events by keyword |
| `rankings(region)` | `/v2/rankings?region=` | ranked teams (`segments`) for a ladder |
| `team_profile(id)` | `/v2/team?id=&q=profile` | identity, roster, rating, winnings |
| `team_matches(id, page)` | `/v2/team?id=&q=matches&page=` | one page of series history |
| `upcoming_matches()` | `/v2/match?q=upcoming` | scheduled `segments` (team1/2, event, unix_timestamp) |
| `match_detail(id)` | `/v2/match/details?match_id=` | single detail segment (maps, players, rounds, vetos, economy, performance) |

`VlrClient(base_url, request_delay=1.5, timeout=30, max_retries=3)`: sleeps `request_delay` before each call (politeness to VLR.gg, not a local limit), retries with linear backoff, raises `ApiError` after exhausting retries.

### Internal: read side (`queries.py`)

All take `(conn, team_id, window=None, events=None, stage=None)` unless noted; window/event/stage default to no-filter via `_scope()`. Detail reads pass `events=None` (LAN/online stays match-level). Return sqlite `Row` lists or computed dicts.

- `list_teams`, `get_team`, `get_roster`, `match_date_bounds`.
- `team_record` -> `{wins, losses, decided}`. `decided_results` -> `["W","L",...]` newest first. `recent_matches` -> framed rows.
- `team_map_results`, `team_rounds`, `team_player_opening`, `team_player_stats`, `team_compositions`, `team_performance`, `team_round_win_types`, `team_economy`, `team_series_results`: per-map/round/player detail, scoped to the team by name, each feeding a specific `stats.*` function (named in the docstrings).
- `team_vetos` (feeds `veto.team_tendencies`), `head_to_head`, `head_to_head_maps`, `common_opponents`, `schedule_strength`, `team_map_opponent_rank`.
- `detail_coverage` -> `{detailed, total}`. `rich_coverage` -> `{economy_maps, performance_matches, win_condition_rounds}`. `team_window_summary` -> `{total, decided, min_date, max_date}`.
- `last_match_date`, `team_rest_load`, `last_roster_change_date`, `player_appearances`, `recent_map_pool` (de-facto current rotation from recent cross-team play), `meeting_maps`, `meeting_lineup`.

### Internal: aggregation (`stats.py`, all pure)

Convention: `_rate(won, total)` returns `None` (never 0) when `total` is falsy. Threshold for "small sample" via `is_small_sample(n, threshold)`.

- Records/form: `form_and_streak`, `infer_match_format`.
- Roster: `classify_roster` (mains/subs/staff from role text), `current_five_names`, `keep_players` (filter to a name set), `merge_player_aliases` + `canonical_player_name` (merge variant spellings), `lineup_continuity`.
- Maps/sides: `map_winrates`, `side_winrates` (derived from winner-only rounds + opposite-side identity), `per_map_splits`, `margin_profile`, `map_compositions`, `map_pool_overlap`, `map_duel_board`.
- Rounds/economy: `pistol_winrate`, `post_pistol_conversion`, `economy_conversion`, `round_win_conditions`.
- Players: `player_aggregates` (round-weighted rate stats + summed counting stats + per-agent breakdown + spread), `player_map_aggregates`, `player_recent_ratings`, `opening_duels`, `team_rating`, `primary_role`, `align_rosters`.
- Performance: `clutch_stats`, `multikill_stats`, `utility_stats`.
- Series: `pressure_stats` (decider/distance/comeback).
- Stats-honesty: `wilson_interval`, `bands_overlap`, `percentile`, `field_summary`, `partition_by_tier`/`tier_of_rank`.
- Comparison framing: `rank_metric_gaps` (sorts per-stat gaps by size, tags per-row leader, never tallies).
- Calibration: `calibration` (scores the user's own predictions by confidence bucket).

### Internal: filters (`window.py`)

`DateWindow(start, end)` (both optional), `.all_time()`, `.clause(column)` -> `(sql, params)` where an all-time window returns `("1=1", [])`. `.contains(value)` mirrors the SQL for Python/tests. `EventFilter(mode=all|lan|online)` and `StageFilter(mode=all|group|playoff)` expose the same `.clause()` shape. All clauses are all-qmark (SQLite forbids mixing named and positional params). Unknown-environment / unclassified-stage rows are excluded from both narrow buckets, included under "all".

### Internal: local user data (`journal.py`)

`pair_key`, `get_note`/`save_note`, `add_log_entry`/`list_log_entries`/`resolve_log_entry`/`update_log_entry`/`delete_log_entry`, `is_favorite`/`add_favorite`/`remove_favorite`/`list_favorites`, `get_upcoming`/`save_upcoming`/`clear_upcoming`. All take an open conn; assume the local tables exist (the app ensures them on startup).

## State Management

- **Persistence**: SQLite is the single source of truth. Scraped data and local user data live in the same file but in disjoint table sets; ingestion never touches the local user tables.
- **Bookkeeping**: `meta` table holds `last_updated` and `last_status`, written by the ingestion engine, read by the freshness banner.
- **Schema evolution**: additive only. `db._ensure_columns()` ALTERs in late-added columns; `ensure_app_tables`/`ensure_analytics_tables` create late-added tables. `backfill_match_stage()` classifies any `match_stage IS NULL` row. All idempotent and run on every app launch, so an older database self-heals without a destructive migration.
- **UI session state** (`st.session_state`): team picks (`team_a`/`team_b` as indices), window mode (`dwmode`/`dwrange`), `active_page`, `env`, `stage`, `view`, `five`, `pool_only`, `palette`, `sections`, `leagues_filter`, the veto simulator selections (`sim_bans`/`sim_picks`), notes text, and `_url_seeded`. Widget callbacks (`_swap_teams`, `_reset_view`) mutate these before the rerun creates the widgets.
- **URL query params**: selection and toggles are mirrored to the query string (`_write_url_state`) and restored once per session (`_apply_url_state`), so a refresh or bookmark reopens the same comparison. Written only on change to avoid a rerun loop. Malformed values are ignored, not forced.
- **Read caching**: every query is wrapped in an `@st.cache_data` `cq_*` function keyed by a `db_key` (the active DB path, so two DB paths never share cache) plus the hashable args. The live connection is passed underscore-prefixed (`_conn`) so Streamlit skips it when hashing. Row lists are converted to plain dicts before caching. A refresh calls `st.cache_data.clear()`.
- **No server state beyond SQLite**: single-user, single-process, local.

## Important Constraints

### Product charter (enforced throughout, not just documented)

- **No composite/winner output.** No overall score, combined rating, "leader" tally across categories, or single best-team output anywhere. The app shows per-statistic values and the signed A-minus-B gap, tags the per-row leader, and stops. `rank_metric_gaps`, `map_pool_overlap`, `calibration`, `team_rating`, `percentile`, and the gap view all carry explicit comments that they never roll up or call a winner. Any extension must hold this line.
- **Data honesty is a feature.** Every figure surfaces its sample size; thin samples get the `(!)` flag and are visually faded in aligned tables; Wilson confidence intervals (`wilson_interval`) and band-overlap tagging (`bands_overlap`) quantify thinness; a stale-team flag, detail-coverage and rich-coverage strips, lineup continuity, rest/load, roster timeline, map-rotation marks, and the patch-era badge all exist to stop a number from misleading.

### Computation rules and edge cases

- **None, not zero.** Rates return `None` when there is nothing to divide, so a blank stat never dilutes an average or prints a misleading 0%. Renderers show a dash.
- **Side win rates from winner-only rounds.** The `rounds` table stores only each round's winner and side; the two teams are always on opposite sides, so a team's attack record = its attack-rounds won plus the opponent's defense-rounds won-against. `pistol_winrate` uses the same identity.
- **Pistols are round 1 and 13 only.** Overtime rounds (25+) are excluded (`is_pistol_round`).
- **Round-weighted means.** Rating, ACS, ADR, KAST, HS% are weighted by each map's round count (matching how VLR sums a season average); K/D, KPR, APR, FK/FD-per-round are summed-then-divided. HS% round-weighting is an explicit approximation (true denominator is hits, not stored).
- **Format and stage are inferred, not stored.** `infer_match_format` reads the series score (>=3 Bo5, 2 Bo3, 1 Bo1). `classify_stage` is a heuristic over the bracket round label and event name; ambiguous labels return `None` (`"unknown"`) and are excluded from both group and playoff.
- **LAN/online is a name heuristic** (`is_lan_event` over `LAN_MARKERS`), only available where the detail pass filled `event_name`.
- **Map pool is inferred, not hardcoded.** `recent_map_pool` (read side) and `active_pool` (veto side) derive the current rotation from recent play so it does not go stale each patch. `CANON_MAPS` gates out non-map junk ("TBD"). `pool_only` hides rotated-out maps; off, they are marked "(rotated out)".
- **Idempotency and resumability.** Ingestion commits per team / per match; every write is an upsert keyed on the VLR id or a delete-then-insert per match. Both passes resume after a failure and never reload stored data.
- **Junk-segment guard.** `_is_real_match` rejects placeholder segments vlrggapi emits past the last real page (match_id equals the team id, empty names).
- **Encoding repair.** vlrggapi returns cp1252-over-utf8 mojibake; `fix_encoding` round-trips it, applied to every scraped string.
- **Name-based detail joins.** Detail tables key teams by name; reads resolve `team_id -> name` once via `_team_name`. A team rename would orphan old detail rows (known fragility).
- **Player id is best-effort.** Detail rows carry only the alias; `_resolve_player_id` matches against `players` and leaves NULL when no roster match exists. `merge_player_aliases` conservatively merges variant spellings (strips a trailing digit run only when >=3 chars remain).

### Known unpopulated / legacy (do not assume data)

- `roster_changes` (transactions endpoint unreliable; history derived from appearances instead).
- `economy` round-level table (VLR exposes no round-by-round economy; `map_economy` aggregate used instead).
- `map_player_stats.clutch_won/clutch_lost` (clutches are series-level only, stored in `match_player_perf`).
- `world_rank` is frequently NULL (VLR does not always expose it).
- No clutch win rate is derived (attempts unavailable, only wins by depth).

### Operational dependencies

- The five vlrggapi **clone patches** (per-side first bloods, round win-condition icon, map pick label, per-map economy block selection, performance-tab name stripping) must be re-applied after any fresh clone, or the affected figures (opening-duel side split, economy, round win conditions, veto pick cross-check) stay empty. The app runs and views fine without them.
- Self-hosted rate limit is ample (600 rpm); the practical constraint is politeness to VLR.gg via `request_delay`.
- Python 3.11+. App deps: streamlit, pandas, requests, plotly. Dev deps add pytest, ruff.

### Testing

`tests/` holds 215 pure-logic tests (heaviest: `test_stats.py` 94, `test_queries.py` 36, `test_match_detail.py` 21, `test_window.py` 17, `test_veto.py` 14). `test_app.py` boots the app against a small seeded hermetic database and exercises views/toggles. The aggregation and split logic is the tested surface, since an error there silently corrupts the comparison. UI is verified by hand (no Playwright by default).

## Extension Notes (for an AI modifying this app)

- **Adding a statistic**: write the pure function in `stats.py` (or `veto.py`) with `None`-not-zero semantics and a unit test with known inputs; add the read in `queries.py` reusing `_scope()`; add a `cq_*` cache wrapper and a `render_*` in `app.py` with a small-sample flag and an empty-state caption. Never emit a composite or winner.
- **Adding a column/table**: append to `schema.sql`, register in `db._ADDED_COLUMNS` or the `_*_TABLES_SQL` strings so older databases self-heal. Keep migrations additive.
- **Adding a filter**: model it as a frozen dataclass in `window.py` with an all-qmark `.clause()`, thread it through `_scope()` and the `cq_*` signatures (it must be hashable to key the cache).
- **Detail-table reads** must resolve `team_id -> name` (`_team_name`) and join `matches` for windowing; team identity inside detail tables is by name.
</content>
</invoke>
