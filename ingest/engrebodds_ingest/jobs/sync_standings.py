"""Daily: league tables for allowlisted leagues playing this week (one request per league)."""

import psycopg

from engrebodds_ingest.api import ApiError
from engrebodds_ingest.config import Settings
from engrebodds_ingest.db import job_run, upsert_teams
from engrebodds_ingest.normalise import TeamRef

DUE = """
select l.id, l.api_league_id, s.season from public.leagues l
  join public.league_seasons s on s.league_id = l.id and s.is_current
 where l.tier <> 'off' and l.type = 'League'
   and coalesce((s.coverage->>'standings')::boolean, false)
   and exists (select 1 from public.fixtures f
                where f.league_id = l.id
                  and f.kickoff_at between now() - interval '2 days' and now() + interval '7 days')
 order by l.is_pinned desc, l.id
 limit %s
"""


def apply_standings(conn: psycopg.Connection, league_id: int, season: int, response: list[dict]) -> int:
    """Replace one league-season's table. Standings aren't track record, so old rows can go."""
    groups = response[0]["league"]["standings"] if response else []
    entries = [row for group in groups for row in group]
    teams = upsert_teams(conn, [TeamRef(e["team"]["id"], e["team"]["name"], e["team"].get("logo")) for e in entries])
    conn.execute("delete from public.standings where league_id = %s and season = %s", (league_id, season))
    with conn.cursor() as cur:
        cur.executemany(
            """insert into public.standings (league_id, season, group_name, team_id, rank, played, won, drawn, lost,
                                             goals_for, goals_against, points, form)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               on conflict (league_id, season, group_name, team_id) do nothing""",
            [(league_id, season, e.get("group") or "", teams[e["team"]["id"]], e["rank"],
              e["all"]["played"], e["all"]["win"], e["all"]["draw"], e["all"]["lose"],
              e["all"]["goals"]["for"], e["all"]["goals"]["against"], e["points"], e.get("form"))
             for e in entries],
        )
    return len(entries)


def run(settings: Settings, limit: int = 200, **_) -> None:
    with job_run(settings, "sync_standings") as ctx:
        due = ctx.conn.execute(DUE, (limit,)).fetchall()
        for league_id, api_league_id, season in due:
            try:
                resp = ctx.api.get("/standings", league=api_league_id, season=season)["response"]
            except ApiError as e:
                ctx.notes.append(f"league {api_league_id} {season}: {e.errors}")
                continue
            ctx.rows += apply_standings(ctx.conn, league_id, season, resp)
        ctx.notes.append(f"{len(due)} leagues due")
