-- VALTrack SQLite schema.
-- The whole data model is defined here up front, even though the cheap
-- list-level harvest in Build Step 2 only populates teams, players, rosters,
-- and matches. The richer tables (map results, per-map player stats, rounds,
-- economy) are filled by the expensive per-match pass in later steps.

-- Identity and roster context. id is the VLR.gg team id.
CREATE TABLE IF NOT EXISTS teams (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    tag           TEXT,
    logo          TEXT,
    country       TEXT,
    country_name  TEXT,
    league        TEXT,          -- americas, emea, pacific, china
    region        TEXT,          -- na, eu, ap, cn (the rankings region code)
    regional_rank INTEGER,       -- rank within the region rankings
    world_rank    INTEGER,       -- left null when VLR does not expose it
    rating        TEXT,
    peak_rating   TEXT,
    streak        TEXT,
    record        TEXT,          -- win-loss string from rankings
    earnings      TEXT,          -- earnings string from rankings
    total_winnings TEXT,         -- total winnings from the team profile
    last_played   TEXT,
    fetched_at    TEXT
);

-- Players seen on any roster. id is the VLR.gg player id.
CREATE TABLE IF NOT EXISTS players (
    id         INTEGER PRIMARY KEY,
    alias      TEXT,
    real_name  TEXT,
    country    TEXT,
    avatar     TEXT,
    fetched_at TEXT
);

-- Current roster snapshot: which players are on which team right now.
CREATE TABLE IF NOT EXISTS rosters (
    team_id    INTEGER NOT NULL,
    player_id  INTEGER NOT NULL,
    is_captain INTEGER DEFAULT 0,
    is_staff   INTEGER DEFAULT 0,
    role       TEXT,
    fetched_at TEXT,
    PRIMARY KEY (team_id, player_id),
    FOREIGN KEY (team_id) REFERENCES teams (id),
    FOREIGN KEY (player_id) REFERENCES players (id)
);

-- Roster change history. Populated in Build Step 13. The transactions endpoint
-- is unreliable in the current vlrggapi version, so this stays empty for now.
CREATE TABLE IF NOT EXISTS roster_changes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id     INTEGER NOT NULL,
    player_id   INTEGER,
    player_name TEXT,
    action      TEXT,             -- join, leave
    date        TEXT,
    ref_url     TEXT,
    fetched_at  TEXT,
    FOREIGN KEY (team_id) REFERENCES teams (id)
);

-- Series-level match history. match_id is the VLR.gg match id, so the same
-- match pulled from both teams' histories collapses to one row.
CREATE TABLE IF NOT EXISTS matches (
    match_id    INTEGER PRIMARY KEY,
    url         TEXT,
    event_round TEXT,             -- the bracket round label, eg "LR2"
    event_name  TEXT,             -- full event name when we can recover it
    date        TEXT,             -- normalized YYYY-MM-DD
    time        TEXT,
    team1_id    INTEGER,
    team1_name  TEXT,
    team1_tag   TEXT,
    team1_logo  TEXT,
    team2_id    INTEGER,
    team2_name  TEXT,
    team2_tag   TEXT,
    team2_logo  TEXT,
    team1_score INTEGER,
    team2_score INTEGER,
    winner_name TEXT,
    map_vetos_raw      TEXT,    -- the raw veto string from the per-match pass
    details_fetched_at TEXT,    -- set once the expensive per-match detail is stored
    match_format       TEXT,    -- bo1, bo3, bo5; inferred from the maps played
    match_stage        TEXT,    -- group, playoff, unknown; classified from the round label
    fetched_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_matches_team1 ON matches (team1_id);
CREATE INDEX IF NOT EXISTS idx_matches_team2 ON matches (team2_id);
CREATE INDEX IF NOT EXISTS idx_matches_date  ON matches (date);

-- Parsed map veto sequence for a match, one row per action in order. Populated
-- by the per-match pass (Build Step 5) from the match's veto string, and
-- consumed by the veto and map-pool reconstruction (Build Step 11). The team is
-- stored as the abbreviation VLR puts in the veto string (eg "PRX"), which is
-- not always the team name, so Build Step 11 resolves it. A "remains" action
-- (the last map left after picks and bans) has no team.
CREATE TABLE IF NOT EXISTS match_vetos (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id   INTEGER NOT NULL,
    seq        INTEGER,           -- order within the veto, starting at 1
    team_token TEXT,              -- the team abbreviation, null for "remains"
    action     TEXT,              -- ban, pick, remains
    map_name   TEXT,
    fetched_at TEXT,
    FOREIGN KEY (match_id) REFERENCES matches (match_id)
);

CREATE INDEX IF NOT EXISTS idx_match_vetos_match ON match_vetos (match_id);

-- Per-map results within a match. Populated by the per-match pass (Build Step 5)
-- and consumed by the side-split work (Build Step 6).
CREATE TABLE IF NOT EXISTS map_results (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id          INTEGER NOT NULL,
    map_name          TEXT,
    map_order         INTEGER,
    team1_name        TEXT,
    team2_name        TEXT,
    team1_score       INTEGER,
    team2_score       INTEGER,
    team1_atk_rounds  INTEGER,
    team1_def_rounds  INTEGER,
    team2_atk_rounds  INTEGER,
    team2_def_rounds  INTEGER,
    winner_name       TEXT,
    fetched_at        TEXT,
    FOREIGN KEY (match_id) REFERENCES matches (match_id)
);

CREATE INDEX IF NOT EXISTS idx_map_results_match ON map_results (match_id);

-- Per-map, per-player statistics. Populated in Build Step 5, aggregated in
-- Build Steps 8, 9, and 10.
CREATE TABLE IF NOT EXISTS map_player_stats (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id     INTEGER NOT NULL,
    map_name     TEXT,
    player_id    INTEGER,
    player_name  TEXT,
    team_name    TEXT,
    agent        TEXT,
    rating       REAL,
    acs          REAL,
    kills        INTEGER,
    deaths       INTEGER,
    assists      INTEGER,
    kast         TEXT,
    adr          REAL,
    hs_pct       TEXT,
    first_kills  INTEGER,
    first_deaths INTEGER,
    -- Per-side opening duels (attack = T side, defense = CT side), used for the
    -- attack and defense opening-duel split in Build Step 8. VLR stores only
    -- per-map per-player totals, not per-round first-blood events, so these are
    -- side totals rather than a round-by-round timeline.
    first_kills_atk  INTEGER,
    first_kills_def  INTEGER,
    first_deaths_atk INTEGER,
    first_deaths_def INTEGER,
    -- Clutch (1vX) situations the player won and lost on the map. These need a
    -- scraper extension to populate (VLR exposes clutches on the match page), so
    -- they stay null until that lands; the clutch view reads them when present.
    clutch_won   INTEGER,
    clutch_lost  INTEGER,
    fetched_at   TEXT,
    FOREIGN KEY (match_id) REFERENCES matches (match_id)
);

CREATE INDEX IF NOT EXISTS idx_map_player_stats_match ON map_player_stats (match_id);
CREATE INDEX IF NOT EXISTS idx_map_player_stats_player ON map_player_stats (player_id);

-- Round-by-round detail. Drives side win rates, pistol rates, and economy
-- conversion in Build Steps 6 and 7.
CREATE TABLE IF NOT EXISTS rounds (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id     INTEGER NOT NULL,
    map_name     TEXT,
    round_number INTEGER,
    winner_side  TEXT,            -- atk, def
    winner_team  TEXT,
    win_type     TEXT,            -- elim, defuse, time, boom
    is_pistol    INTEGER DEFAULT 0,
    fetched_at   TEXT,
    FOREIGN KEY (match_id) REFERENCES matches (match_id)
);

CREATE INDEX IF NOT EXISTS idx_rounds_match ON rounds (match_id);

-- Per-round economy detail. Intended to drive eco and anti-eco conversion in
-- Build Step 7, but left empty for now: vlrggapi's match detail returns only the
-- first map's economy table for every map, so per-map economy is not reliably
-- available from the data source yet. Pistol-round win rate is computed from the
-- rounds table instead, which is reliable.
CREATE TABLE IF NOT EXISTS economy (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id     INTEGER NOT NULL,
    map_name     TEXT,
    round_number INTEGER,
    team_name    TEXT,
    buy_type     TEXT,            -- eco, semi, full, bonus
    bank         INTEGER,
    outcome      TEXT,            -- won, lost
    fetched_at   TEXT,
    FOREIGN KEY (match_id) REFERENCES matches (match_id)
);

CREATE INDEX IF NOT EXISTS idx_economy_match ON economy (match_id);

-- Small key/value store for ingestion bookkeeping (last update time, last
-- status). Used by the in-app refresh and staleness banner in Build Step 16.
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Local-only reasoning aids (Build Step 15). These hold the user's own input,
-- never anything scraped, so they are not touched by ingestion. The app ensures
-- they exist on startup, since it does not run the full schema.

-- A free-text note per team pair, keyed by the two ids sorted "min-max" so the
-- same pair maps to one note whichever way it is selected.
CREATE TABLE IF NOT EXISTS matchup_notes (
    pair_key   TEXT PRIMARY KEY,
    body       TEXT,
    updated_at TEXT
);

-- A personal matchup log: record a matchup with a pre-match note and confidence,
-- then later record the actual outcome and review past entries.
CREATE TABLE IF NOT EXISTS matchup_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    team_a_id    INTEGER,
    team_a_name  TEXT,
    team_b_id    INTEGER,
    team_b_name  TEXT,
    note          TEXT,
    confidence    TEXT,
    predicted_side TEXT,         -- the team the user leaned toward, "a" or "b", for calibration
    outcome       TEXT,          -- null until the user records the result
    outcome_side  TEXT,          -- structured winner, "a" or "b", null until resolved
    created_at    TEXT,
    resolved_at   TEXT
);

-- Saved (favorite) matchups for one-click reload, keyed by the order-independent
-- pair key like the notes table.
CREATE TABLE IF NOT EXISTS matchup_favorites (
    pair_key    TEXT PRIMARY KEY,
    team_a_id   INTEGER,
    team_a_name TEXT,
    team_b_id   INTEGER,
    team_b_name TEXT,
    created_at  TEXT
);

-- An optional tag for the upcoming real match between a pair: its date, event,
-- and whether it is LAN, so the context panel can compare the data against the
-- actual match conditions. Keyed by the order-independent pair key.
CREATE TABLE IF NOT EXISTS matchup_upcoming (
    pair_key    TEXT PRIMARY KEY,
    match_date  TEXT,
    event_name  TEXT,
    is_lan      INTEGER,
    updated_at  TEXT
);
