"""Every 15 minutes on match days: live and unfinished fixtures, then grading and odds thinning.

/fixtures?ids= takes up to 20 fixtures per request and includes each match's statistics,
so most statistics arrive here without extra requests (sync_stats fills the gaps).
The free plan refuses `ids`; then the job falls back to /fixtures?date= (one request per date, no statistics).
"""

from datetime import UTC, datetime

from engrebodds_ingest.api import ApiError
from engrebodds_ingest.config import Settings
from engrebodds_ingest.db import grade_all, job_run, upsert_fixtures, upsert_team_stats
from engrebodds_ingest.normalise import fixture_row, team_stats_rows

BATCH = 20

DUE = """
select api_fixture_id, (kickoff_at at time zone 'UTC')::date from public.fixtures
 where kickoff_at <= now() and kickoff_at > now() - interval '3 days'
   and status not in ('FT', 'AET', 'PEN', 'CANC', 'ABD', 'AWD', 'WO')
   and not manual_score
 order by kickoff_at
"""

# Matches over for 3+ hours whose odds history hasn't been thinned yet.
TO_THIN = """
select f.id from public.fixtures f
 where f.odds_thinned_at is null
   and f.kickoff_at < now() - interval '3 hours'
   and f.status in ('FT', 'AET', 'PEN', 'CANC', 'ABD', 'AWD', 'WO')
   and exists (select 1 from public.odds_snapshots o where o.fixture_id = f.id)
"""


def fetch_due(api, due: list[tuple[int, object]], notes: list[str]):
    """Yield batches of fixture items for the due fixtures: by ids, or by date when ids are refused."""
    ids = [api_id for api_id, _ in due]
    for i in range(0, len(ids), BATCH):
        try:
            yield api.get("/fixtures", ids="-".join(map(str, ids[i:i + BATCH])))["response"]
        except ApiError as e:
            if not (isinstance(e.errors, dict) and "plan" in e.errors):
                raise
            notes.append(f"ids refused ({e.errors['plan']}); fetching by date")
            wanted = set(ids[i:])
            for day in sorted({d for api_id, d in due if api_id in wanted}):
                try:
                    items = api.get_all("/fixtures", date=day.isoformat())
                except ApiError as date_error:
                    # The free plan only serves yesterday to tomorrow; older dates wait for the Pro plan.
                    if not (isinstance(date_error.errors, dict) and "plan" in date_error.errors):
                        raise
                    notes.append(f"{day}: date refused by the plan, skipped")
                    continue
                yield [x for x in items if x["fixture"]["id"] in wanted]
            return


def run(settings: Settings, **_) -> None:
    with job_run(settings, "sync_results") as ctx:
        now = datetime.now(UTC)
        due = ctx.conn.execute(DUE).fetchall()
        with_stats = 0
        for items in fetch_due(ctx.api, due, ctx.notes):
            ids, notes = upsert_fixtures(ctx.conn, [fixture_row(x) for x in items], now)
            ctx.rows += len(ids)
            ctx.notes += notes
            for item in items:
                fid = ids.get(item["fixture"]["id"])
                if fid and item["fixture"]["status"]["short"] in ("FT", "AET", "PEN"):
                    written = upsert_team_stats(ctx.conn, fid, team_stats_rows(item.get("statistics") or []))
                    with_stats += bool(written)
                    ctx.rows += written
        ctx.notes.append(f"{len(due)} fixtures due, statistics stored for {with_stats}")

        thinned = sum(ctx.conn.execute("select public.thin_odds(%s)", (fid,)).fetchone()[0]
                      for (fid,) in ctx.conn.execute(TO_THIN).fetchall())
        ctx.notes.append(f"odds rows thinned: {thinned}")
        ctx.notes.append(f"graded: {grade_all(ctx.conn)}")
