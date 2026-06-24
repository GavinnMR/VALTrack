"""The VALTrack ingestion engine.

One engine, two scopes. A full run loads every franchise team's whole match
history; an incremental run stops at the first already-stored match per team,
since history comes back newest first. This module uses only the cheap endpoints:
rankings, team profile, and team match history. The expensive per-match detail
pass is handled separately.

Writes happen team by team and commit per team, so a failure partway through
keeps the teams already loaded. Every write is an upsert keyed on the VLR id, so
re-running is safe.
"""
from datetime import datetime, timezone

from valtrack import db
from valtrack.api_client import ApiError, VlrClient
from valtrack.cleaning import fix_encoding, parse_date, parse_int, parse_score
from valtrack.franchise import LEAGUE_RANKING_REGIONS, iter_franchise_teams
from valtrack.match_detail import parse_match_detail, store_match_detail


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch_rankings(client, region):
    """Return a name -> ranking-fields lookup for a region.

    Names are fixed for encoding and casefolded for lookup, since rankings key
    teams by name and the profile names can differ in case.
    """
    data = client.rankings(region)
    lookup = {}
    for seg in data.get("segments", []):
        name = fix_encoding(seg.get("team", ""))
        lookup[name.casefold()] = {
            "regional_rank": parse_int(seg.get("rank")),
            "record": fix_encoding(seg.get("record")),
            "earnings": fix_encoding(seg.get("earnings")),
            "last_played": fix_encoding(seg.get("last_played")),
        }
    return lookup


def _get_region_rankings(client, cache, region):
    """Return a region's ranking lookup, caching once. A ladder that cannot be
    fetched is cached empty so one bad region does not abort a team."""
    if region not in cache:
        try:
            cache[region] = fetch_rankings(client, region)
        except ApiError:
            cache[region] = {}
    return cache[region]


def resolve_ranking(client, cache, league, profile_name, fallback_name):
    """Find a team's regional ranking across its league's ladders.

    VLR splits a franchise league across several ranking ladders (see
    LEAGUE_RANKING_REGIONS), so we search them in priority order and take the
    first name match. Returns None for teams that sit on no ladder, which is the
    honest result for inactive orgs rather than a fabricated rank.
    """
    keys = [profile_name.casefold(), fallback_name.casefold()]
    for region in LEAGUE_RANKING_REGIONS.get(league, []):
        lookup = _get_region_rankings(client, cache, region)
        for key in keys:
            if key in lookup:
                return lookup[key]
    return None


def upsert_team(conn, league, region, profile, ranking):
    """Insert or update a team's identity, ranking, and rating fields."""
    rating = profile.get("rating") or {}
    ranking = ranking or {}
    conn.execute(
        """
        INSERT INTO teams (
            id, name, tag, logo, country, country_name, league, region,
            regional_rank, world_rank, rating, peak_rating, streak, record,
            earnings, total_winnings, last_played, fetched_at
        ) VALUES (
            :id, :name, :tag, :logo, :country, :country_name, :league, :region,
            :regional_rank, :world_rank, :rating, :peak_rating, :streak, :record,
            :earnings, :total_winnings, :last_played, :fetched_at
        )
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name, tag=excluded.tag, logo=excluded.logo,
            country=excluded.country, country_name=excluded.country_name,
            league=excluded.league, region=excluded.region,
            regional_rank=excluded.regional_rank, world_rank=excluded.world_rank,
            rating=excluded.rating, peak_rating=excluded.peak_rating,
            streak=excluded.streak, record=excluded.record,
            earnings=excluded.earnings, total_winnings=excluded.total_winnings,
            last_played=excluded.last_played, fetched_at=excluded.fetched_at
        """,
        {
            "id": parse_int(profile.get("id")),
            "name": fix_encoding(profile.get("name")),
            "tag": fix_encoding(profile.get("tag")),
            "logo": profile.get("logo"),
            "country": profile.get("country"),
            "country_name": fix_encoding(profile.get("country_name")),
            "league": league,
            "region": region,
            "regional_rank": ranking.get("regional_rank"),
            "world_rank": parse_int(rating.get("rank")),
            "rating": fix_encoding(rating.get("rating")),
            "peak_rating": fix_encoding(rating.get("peak_rating")),
            "streak": fix_encoding(rating.get("streak")),
            "record": ranking.get("record"),
            "earnings": ranking.get("earnings"),
            "total_winnings": fix_encoding(profile.get("total_winnings")),
            "last_played": ranking.get("last_played"),
            "fetched_at": _now(),
        },
    )


def upsert_roster(conn, team_id, roster):
    """Replace a team's current roster snapshot with the latest profile roster."""
    now = _now()
    conn.execute("DELETE FROM rosters WHERE team_id = ?", (team_id,))
    for member in roster:
        player_id = parse_int(member.get("id"))
        if player_id is None:
            continue
        conn.execute(
            """
            INSERT INTO players (id, alias, real_name, country, avatar, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                alias=excluded.alias, real_name=excluded.real_name,
                country=excluded.country, avatar=excluded.avatar,
                fetched_at=excluded.fetched_at
            """,
            (
                player_id,
                fix_encoding(member.get("alias")),
                fix_encoding(member.get("real_name")),
                member.get("country"),
                member.get("avatar"),
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO rosters (
                team_id, player_id, is_captain, is_staff, role, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                team_id,
                player_id,
                1 if member.get("is_captain") else 0,
                1 if member.get("is_staff") else 0,
                fix_encoding(member.get("role")),
                now,
            ),
        )


def _opponent_id(conn, name):
    if not name:
        return None
    row = conn.execute(
        "SELECT id FROM teams WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    return row["id"] if row else None


def upsert_match(conn, source_team_id, match):
    """Insert or update one series-level match row. Returns the match id.

    The queried team is team1 in this endpoint. The opponent id is filled in
    only when the opponent is a team we already store.
    """
    match_id = parse_int(match.get("match_id"))
    if match_id is None:
        return None

    team1 = match.get("team1") or {}
    team2 = match.get("team2") or {}
    t1_name = fix_encoding(team1.get("name"))
    t2_name = fix_encoding(team2.get("name"))
    t1_score, t2_score = parse_score(match.get("score"))

    winner = None
    if t1_score is not None and t2_score is not None:
        if t1_score > t2_score:
            winner = t1_name
        elif t2_score > t1_score:
            winner = t2_name

    conn.execute(
        """
        INSERT INTO matches (
            match_id, url, event_round, event_name, date, time,
            team1_id, team1_name, team1_tag, team1_logo,
            team2_id, team2_name, team2_tag, team2_logo,
            team1_score, team2_score, winner_name, fetched_at
        ) VALUES (
            :match_id, :url, :event_round, :event_name, :date, :time,
            :team1_id, :team1_name, :team1_tag, :team1_logo,
            :team2_id, :team2_name, :team2_tag, :team2_logo,
            :team1_score, :team2_score, :winner_name, :fetched_at
        )
        ON CONFLICT(match_id) DO UPDATE SET
            url=excluded.url, event_round=excluded.event_round,
            date=excluded.date, time=excluded.time,
            team1_id=COALESCE(excluded.team1_id, matches.team1_id),
            team1_name=excluded.team1_name, team1_tag=excluded.team1_tag,
            team1_logo=excluded.team1_logo,
            team2_id=COALESCE(excluded.team2_id, matches.team2_id),
            team2_name=excluded.team2_name, team2_tag=excluded.team2_tag,
            team2_logo=excluded.team2_logo,
            team1_score=excluded.team1_score, team2_score=excluded.team2_score,
            winner_name=excluded.winner_name, fetched_at=excluded.fetched_at
        """,
        {
            "match_id": match_id,
            "url": match.get("url"),
            "event_round": fix_encoding(match.get("event")),
            "event_name": None,  # real event name comes with the per-match pass
            "date": parse_date(match.get("date")),
            "time": match.get("time"),
            "team1_id": source_team_id,
            "team1_name": t1_name,
            "team1_tag": fix_encoding(team1.get("tag")),
            "team1_logo": team1.get("logo"),
            "team2_id": _opponent_id(conn, t2_name),
            "team2_name": t2_name,
            "team2_tag": fix_encoding(team2.get("tag")),
            "team2_logo": team2.get("logo"),
            "team1_score": t1_score,
            "team2_score": t2_score,
            "winner_name": winner,
            "fetched_at": _now(),
        },
    )
    return match_id


def _is_real_match(match):
    """Reject the junk segments the endpoint emits past the last real page.

    Once paging runs off the end of a team's history, vlrggapi does not return
    an empty page. It parses a nav link and emits placeholder segments whose
    match_id is the team id and whose team names, date, and score are all empty.
    A real played match always carries both team names, so that is the tell.
    """
    team1 = match.get("team1") or {}
    team2 = match.get("team2") or {}
    return bool(fix_encoding(team1.get("name"))) and bool(
        fix_encoding(team2.get("name"))
    )


def ingest_team_matches(client, conn, team_id, scope, max_pages=100):
    """Page a team's match history and upsert rows.

    Full scope reads to the end. Incremental scope stops at the first match
    already stored, since the history is newest first. Returns the count of
    matches written or refreshed.

    The endpoint does not signal the end with an empty page; past the last real
    page it repeats placeholder junk (see _is_real_match). So we stop when a page
    contributes no new real match, and also track ids seen this run to guard
    against any clamped page that repeats real rows.
    """
    written = 0
    seen = set()
    for page in range(1, max_pages + 1):
        data = client.team_matches(team_id, page=page)
        segments = data.get("segments", [])
        if not segments:
            break

        new_this_page = 0
        hit_known = False
        for match in segments:
            if not _is_real_match(match):
                continue
            match_id = parse_int(match.get("match_id"))
            if match_id is None or match_id in seen:
                continue
            seen.add(match_id)
            if scope == "incremental":
                exists = conn.execute(
                    "SELECT 1 FROM matches WHERE match_id = ?", (match_id,)
                ).fetchone()
                if exists:
                    hit_known = True
                    break
            if upsert_match(conn, team_id, match) is not None:
                written += 1
                new_this_page += 1

        if scope == "incremental" and hit_known:
            break
        # No new real match on a full page means we have run off the end.
        if new_this_page == 0:
            break
    return written


def link_match_teams(conn):
    """Fill in opponent ids by name once all franchise teams are loaded.

    During the team-by-team pass an opponent that has not been loaded yet leaves
    team2_id null. After the run we backfill any null id whose name matches a
    franchise team, so head-to-head and common-opponent queries can join on id.
    Non-franchise opponents are not stored as teams, so their id stays null.
    """
    conn.execute(
        "UPDATE matches SET team1_id = "
        "(SELECT id FROM teams WHERE name = matches.team1_name COLLATE NOCASE) "
        "WHERE team1_id IS NULL"
    )
    conn.execute(
        "UPDATE matches SET team2_id = "
        "(SELECT id FROM teams WHERE name = matches.team2_name COLLATE NOCASE) "
        "WHERE team2_id IS NULL"
    )


def matches_needing_detail(conn, since=None):
    """match_ids that are decided but have no per-match detail stored yet.

    A match is "done" once details_fetched_at is set, even when it parsed to zero
    maps (a forfeit), so those are not refetched forever. Undecided matches with
    no score are skipped, since there is no detail to pull. Newest first, because
    recent matches are the most relevant to look at. `since` (an ISO date) bounds
    the selection to matches on or after that date, for a windowed backfill.
    """
    params = []
    date_clause = ""
    if since:
        date_clause = "AND date >= ? "
        params.append(since)
    rows = conn.execute(
        "SELECT match_id FROM matches "
        "WHERE details_fetched_at IS NULL AND team1_score IS NOT NULL "
        f"{date_clause}"
        "ORDER BY date DESC, match_id DESC",
        params,
    ).fetchall()
    return [row["match_id"] for row in rows]


def matches_missing_analytics(conn, since=None):
    """match_ids to re-detail so the newer rich tables (economy, performance) fill.

    The detail pass shipped before the per-map economy and series-performance
    tables existed, so most already-detailed matches carry the core detail (maps,
    rounds, players) but no map_economy rows. This selects those alongside any
    brand-new undetailed match, so one re-detail run fills the gap:

      - never detailed (details_fetched_at IS NULL), or
      - detailed and has maps, but no map_economy rows (a pre-patch detail).

    A forfeit (detailed, zero maps) has no map_results, so it is not reselected,
    and a match re-detailed with the patched scraper gains economy rows and drops
    out, so this stays re-runnable. `since` (an ISO date) bounds it to a recent
    window, which is the point: a scout does not need years-old economy, so the
    backfill is kept to roughly the last year or two rather than the full table.
    Newest first.
    """
    params = []
    date_clause = ""
    if since:
        date_clause = "AND m.date >= ? "
        params.append(since)
    rows = conn.execute(
        f"""
        SELECT m.match_id FROM matches m
        WHERE m.team1_score IS NOT NULL
          AND (
            m.details_fetched_at IS NULL
            OR (
              EXISTS (SELECT 1 FROM map_results mr WHERE mr.match_id = m.match_id)
              AND NOT EXISTS (
                  SELECT 1 FROM map_economy me WHERE me.match_id = m.match_id)
            )
          )
          {date_clause}
        ORDER BY m.date DESC, m.match_id DESC
        """,
        params,
    ).fetchall()
    return [row["match_id"] for row in rows]


def ingest_match_details(
    client, conn, scope="full", limit=None, progress=print,
    redetail=False, since=None,
):
    """The expensive per-match pass: pull detail for each match missing it.

    One API call per match, so this is the bulk of the harvest time. It is
    re-runnable and incremental-aware: it only touches matches without stored
    detail, and commits per match so a failure partway through keeps finished
    matches. Full and incremental select the same set (matches still lacking
    detail); the scope name is kept for parity with run_ingest. limit caps how
    many matches to process in one run, for batching or a quick smoke test.

    When redetail is set, the selection switches to matches missing the newer
    economy and performance tables (plus any undetailed match), so a bounded
    re-detail backfills the rich tables on matches detailed before those tables
    existed. `since` (an ISO date) bounds either selection to a recent window.
    """
    if scope not in ("full", "incremental"):
        raise ValueError(f"unknown scope: {scope}")

    if redetail:
        ids = matches_missing_analytics(conn, since)
    else:
        ids = matches_needing_detail(conn, since)
    if limit is not None:
        ids = ids[:limit]

    summary = {"matches": 0, "maps": 0, "errors": []}
    try:
        for match_id in ids:
            try:
                segment = client.match_detail(match_id)
                parsed = parse_match_detail(segment)
                store_match_detail(conn, match_id, parsed)
                conn.commit()  # per match, so a later failure keeps this one
                summary["matches"] += 1
                summary["maps"] += len(parsed["maps"])
                progress(f"  ok match {match_id}: {len(parsed['maps'])} maps")
            except ApiError as exc:
                conn.rollback()
                summary["errors"].append(match_id)
                progress(f"  ! API error on match {match_id}: {exc}")

        db.set_meta(conn, "last_updated", _now())
        db.set_meta(conn, "last_status", "ok" if not summary["errors"] else "partial")
        conn.commit()
    except Exception:
        db.set_meta(conn, "last_status", "failed")
        conn.commit()
        raise

    return summary


def run_detail_ingest(scope="full", client=None, db_path=db.DB_PATH,
                      limit=None, progress=print, redetail=False, since=None):
    """Open a connection and run the per-match detail pass over all teams.

    The terminal entry point for the expensive pass. The cheap list-level harvest
    (run_ingest) must have populated matches first. redetail and since are passed
    through for a bounded re-detail that fills the newer economy and performance
    tables (see ingest_match_details).
    """
    if scope not in ("full", "incremental"):
        raise ValueError(f"unknown scope: {scope}")

    client = client or VlrClient()
    db.init_db(db_path)
    conn = db.connect(db_path)
    try:
        return ingest_match_details(
            client, conn, scope, limit, progress, redetail=redetail, since=since)
    finally:
        conn.close()


def run_ingest(scope="full", client=None, db_path=db.DB_PATH, progress=print):
    """Run the cheap harvest over all franchise teams.

    scope is "full" or "incremental". progress is a callable for status lines so
    a terminal run and the future in-app run can report differently.
    """
    if scope not in ("full", "incremental"):
        raise ValueError(f"unknown scope: {scope}")

    client = client or VlrClient()
    db.init_db(db_path)
    conn = db.connect(db_path)

    summary = {"teams": 0, "unresolved": [], "matches": 0, "errors": []}
    rankings_cache = {}

    try:
        for league, region, name, team_id in iter_franchise_teams():
            try:
                data = client.team_profile(team_id)
                segments = data.get("segments", [])
                if not segments:
                    progress(f"  ! no profile for {name} (id {team_id})")
                    summary["errors"].append(name)
                    continue
                profile = segments[0]

                ranking = resolve_ranking(
                    client,
                    rankings_cache,
                    league,
                    fix_encoding(profile.get("name", "")),
                    name,
                )
                if ranking is None:
                    summary["unresolved"].append(name)

                upsert_team(conn, league, region, profile, ranking)
                upsert_roster(conn, team_id, profile.get("roster", []))
                count = ingest_team_matches(client, conn, team_id, scope)

                conn.commit()  # commit per team so progress survives a later failure
                summary["teams"] += 1
                summary["matches"] += count
                progress(f"  ok {name} (id {team_id}): {count} matches")
            except ApiError as exc:
                conn.rollback()
                summary["errors"].append(name)
                progress(f"  ! API error on {name}: {exc}")

        link_match_teams(conn)
        db.set_meta(conn, "last_updated", _now())
        db.set_meta(conn, "last_status", "ok" if not summary["errors"] else "partial")
        conn.commit()
    except Exception:
        db.set_meta(conn, "last_status", "failed")
        conn.commit()
        raise
    finally:
        conn.close()

    return summary
