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
    fetched_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_matches_team1 ON matches (team1_id);
CREATE INDEX IF NOT EXISTS idx_matches_team2 ON matches (team2_id);
CREATE INDEX IF NOT EXISTS idx_matches_date  ON matches (date);

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

-- Per-round economy detail. Drives eco and anti-eco conversion in Build Step 7.
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
