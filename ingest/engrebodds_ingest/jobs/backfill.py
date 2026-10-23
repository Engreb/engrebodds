"""Nightly: past seasons of fixtures and results for allowlisted leagues, one request per league-season.

Works through league-seasons not yet loaded (league_seasons.fixtures_backfilled_at is null), pinned
and full-tier leagues first, and saves progress after each one. It stops cleanly at the daily quota
floor, which is kept free for the live jobs.

Free plan: only seasons labelled 2022-2024 are readable (the defaults). The statistics backfill
(one request per match, ~90,000 requests) waits for Pro.
"""

from datetime import UTC, datetime

import psycopg

from engrebodds_ingest.api import ApiError, ApiFootball, QuotaExhausted
from engrebodds_ingest.config import Settings
from engrebodds_ingest.db import job_run, upsert_fixtures
from engrebodds_ingest.normalise import fixture_row

DUE = """
select s.league_id, l.api_league_id, s.season, l.name
  from public.league_seasons s
  join public.leagues l on l.id = s.league_id
 where l.tier <> 'off'
   and not s.is_current
   and s.fixtures_backfilled_at is null
   and s.season between %(first)s and %(last)s
 order by l.is_pinned desc, (l.tier = 'full') desc, (l.type = 'League') desc, s.season desc, l.id
 limit %(limit)s
"""

REMAINING = """
select count(*) from public.league_seasons s join public.leagues l on l.id = s.league_id
 where l.tier <> 'off' and not s.is_current and s.fixtures_backfilled_at is null
   and s.season between %(first)s and %(last)s
"""

MAX_ISSUE_NOTES = 20


def load_season(conn: psycopg.Connection, api: ApiFootball, league_id: int, api_league_id: int,
                season: int) -> tuple[dict[int, int], list[str]]:
    """Fetch and store one league-season, and mark it loaded. Doesn't commit."""
    items = api.get_all("/fixtures", league=api_league_id, season=season)
    ids, notes = upsert_fixtures(conn, [fixture_row(x) for x in items], datetime.now(UTC))
    conn.execute(
        "update public.league_seasons set fixtures_backfilled_at = now() where league_id = %s and season = %s",
        (league_id, season))
    return ids, notes


def run(settings: Settings, limit: int = 100, first_season: int = 2022, last_season: int = 2024, **_) -> None:
    window = {"first": first_season, "last": last_season, "limit": limit}
    with job_run(settings, "backfill", quota_floor=settings.quota_floor) as ctx:
        due = ctx.conn.execute(DUE, window).fetchall()
        loaded, issues = 0, []
        for league_id, api_league_id, season, name in due:
            try:
                ids, notes = load_season(ctx.conn, ctx.api, league_id, api_league_id, season)
            except QuotaExhausted as e:
                ctx.notes.append(f"stopped at the quota floor: {e}")
                break
            except ApiError as e:
                ctx.conn.rollback()
                ctx.notes.append(f"{name} {season}: {e.errors}")
                continue
            ctx.conn.commit()   # keep each league-season even if a later request fails
            ctx.rows += len(ids)
            issues += notes
            loaded += 1

        ctx.notes += issues[:MAX_ISSUE_NOTES]
        if len(issues) > MAX_ISSUE_NOTES:
            ctx.notes.append(f"... and {len(issues) - MAX_ISSUE_NOTES} more fixtures with flagged scores")
        remaining = ctx.conn.execute(REMAINING, window).fetchone()[0]
        ctx.notes.append(f"{loaded} league-seasons loaded ({first_season}-{last_season}), {remaining} still to do")
