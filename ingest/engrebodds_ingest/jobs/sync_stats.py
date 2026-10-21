"""Hourly fallback: statistics for finished full-tier matches that sync_results didn't get them for.

Gives up on a match 3 days after kick-off; grading then voids stats tips on it after 72h.
"""

from engrebodds_ingest.api import ApiError
from engrebodds_ingest.config import Settings
from engrebodds_ingest.db import grade_all, job_run, upsert_team_stats
from engrebodds_ingest.normalise import team_stats_rows

DUE = """
select f.id, f.api_fixture_id from public.fixtures f
  join public.leagues l on l.id = f.league_id
 where l.tier = 'full'
   and f.status in ('FT', 'AET', 'PEN')
   and f.stats_fetched_at is null
   and f.kickoff_at between now() - interval '3 days' and now() - interval '2 hours'
 order by f.kickoff_at
 limit %s
"""


def run(settings: Settings, limit: int = 100, **_) -> None:
    with job_run(settings, "sync_stats") as ctx:
        due = ctx.conn.execute(DUE, (limit,)).fetchall()
        missing = 0
        for fid, api_id in due:
            try:
                resp = ctx.api.get("/fixtures/statistics", fixture=api_id)["response"]
            except ApiError as e:
                ctx.notes.append(f"fixture {api_id}: {e.errors}")
                continue
            written = upsert_team_stats(ctx.conn, fid, team_stats_rows(resp))
            missing += not written
            ctx.rows += written
        ctx.notes.append(f"{len(due)} fixtures due, {missing} still without statistics")
        ctx.notes.append(f"graded: {grade_all(ctx.conn)}")
