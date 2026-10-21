"""Weekly: every league with its seasons and coverage, and each league's tier (1 request).

Tier rule (agreed after the coverage audit):
  full  = current season has odds and match statistics
  goals = current season has odds
  off   = neither (stored, nothing published)
Pinned leagues (NPFL, CAF; set in the migration) are never off.
Teams arrive with fixtures, so no /teams requests are needed.
"""

import psycopg
from psycopg.types.json import Jsonb

from engrebodds_ingest.config import Settings
from engrebodds_ingest.db import job_run

SEASONS_KEPT = 6   # current season and the five before it


def current_season(entry: dict) -> dict | None:
    return next((s for s in entry["seasons"] if s.get("current")), None)


def tier_for(entry: dict) -> str:
    season = current_season(entry)
    cov = (season or {}).get("coverage") or {}
    if not cov.get("odds"):
        return "off"
    return "full" if (cov.get("fixtures") or {}).get("statistics_fixtures") else "goals"


def apply_leagues(conn: psycopg.Connection, entries: list[dict]) -> int:
    """Upsert leagues and their recent seasons. Returns rows written."""
    with conn.cursor() as cur:
        cur.executemany(
            """insert into public.leagues (api_league_id, name, country, country_code, type, logo_url, tier)
               values (%s, %s, %s, %s, %s, %s, %s)
               on conflict (api_league_id) do update set
                 name = excluded.name, country = excluded.country, country_code = excluded.country_code,
                 type = excluded.type, logo_url = excluded.logo_url,
                 tier = case when leagues.is_pinned and excluded.tier = 'off' then 'goals' else excluded.tier end""",
            [(e["league"]["id"], e["league"]["name"], e["country"]["name"], e["country"].get("code"),
              e["league"]["type"] if e["league"]["type"] in ("League", "Cup") else "League",
              e["league"].get("logo"), tier_for(e)) for e in entries],
        )
    ids = dict(conn.execute("select api_league_id, id from public.leagues").fetchall())

    seasons = []
    for e in entries:
        recent = sorted(e["seasons"], key=lambda s: s["year"])[-SEASONS_KEPT:]
        for s in recent:
            seasons.append((ids[e["league"]["id"]], s["year"], s.get("start"), s.get("end"),
                            bool(s.get("current")), Jsonb(s.get("coverage") or {})))
    with conn.cursor() as cur:
        cur.executemany(
            """insert into public.league_seasons (league_id, season, start_date, end_date, is_current, coverage)
               values (%s, %s, %s, %s, %s, %s)
               on conflict (league_id, season) do update set
                 start_date = excluded.start_date, end_date = excluded.end_date,
                 is_current = excluded.is_current, coverage = excluded.coverage""",
            seasons,
        )
    return len(entries) + len(seasons)


def run(settings: Settings, **_) -> None:
    with job_run(settings, "sync_reference") as ctx:
        entries = ctx.api.get("/leagues")["response"]
        ctx.rows += apply_leagues(ctx.conn, entries)
        tiers = dict(ctx.conn.execute("select tier, count(*) from public.leagues group by tier").fetchall())
        ctx.notes.append(f"tiers: {tiers}")
