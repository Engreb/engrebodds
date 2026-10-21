"""Database access: job-run logging and the upserts shared by several jobs.

Helpers never commit; job_run commits once at the end, so a failed job leaves no half-written data.
"""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import psycopg
from psycopg.types.json import Jsonb

from engrebodds_ingest.api import ApiFootball
from engrebodds_ingest.config import Settings
from engrebodds_ingest.normalise import FINAL, FixtureRow, TeamRef, TeamStatsRow, fixture_issues


@dataclass
class JobContext:
    api: ApiFootball
    conn: psycopg.Connection
    rows: int = 0
    notes: list[str] = field(default_factory=list)


@contextmanager
def job_run(settings: Settings, job: str, *, quota_floor: int = 0) -> Iterator[JobContext]:
    """Open the API client and a connection, and record the run in ingestion_runs."""
    api = ApiFootball(settings.api_key, quota_floor=quota_floor)
    conn = psycopg.connect(settings.database_url)
    run_id = conn.execute("insert into public.ingestion_runs (job) values (%s) returning id", (job,)).fetchone()[0]
    conn.commit()
    ctx = JobContext(api, conn)
    status, error = "failed", None
    try:
        yield ctx
        conn.commit()
        status = "ok"
    except BaseException as e:
        conn.rollback()
        error = repr(e)[:2000]
        raise
    finally:
        conn.execute(
            """update public.ingestion_runs
                  set finished_at = now(), status = %s, requests_used = %s, quota_remaining = %s,
                      rows_written = %s, notes = %s, error = %s
                where id = %s""",
            (status, api.requests_made, api.quota_remaining, ctx.rows, Jsonb(ctx.notes), error, run_id),
        )
        conn.commit()
        conn.close()
        api.close()
        print(f"{job}: {status}, {ctx.rows} rows, {api.requests_made} requests, "
              f"{len(ctx.notes)} notes, quota left {api.quota_remaining}")


def active_leagues(conn: psycopg.Connection) -> dict[int, int]:
    """api_league_id -> league id, for leagues on the allowlist (tier full or goals)."""
    return dict(conn.execute("select api_league_id, id from public.leagues where tier <> 'off'").fetchall())


def upsert_teams(conn: psycopg.Connection, teams: Iterable[TeamRef]) -> dict[int, int]:
    """Insert or refresh teams; return api_team_id -> team id."""
    uniq = {t.api_team_id: t for t in teams}
    if not uniq:
        return {}
    with conn.cursor() as cur:
        cur.executemany(
            """insert into public.teams (api_team_id, name, logo_url) values (%s, %s, %s)
               on conflict (api_team_id) do update set name = excluded.name, logo_url = excluded.logo_url
               where (teams.name, teams.logo_url) is distinct from (excluded.name, excluded.logo_url)""",
            [(t.api_team_id, t.name, t.logo_url) for t in uniq.values()],
        )
    return dict(conn.execute("select api_team_id, id from public.teams where api_team_id = any(%s)",
                             (list(uniq),)).fetchall())


# Score columns are left alone once someone has corrected a fixture by hand (manual_score).
_SCORE_COLS = ("status", "home_ht", "away_ht", "home_90", "away_90", "home_et", "away_et",
               "home_pen", "away_pen", "score_issue")
_FIXTURE_COLS = ("api_fixture_id", "league_id", "season", "round", "kickoff_at", "home_team_id", "away_team_id",
                 "elapsed", "finished_at") + _SCORE_COLS
_UPSERT_FIXTURE = (
    f"insert into public.fixtures ({', '.join(_FIXTURE_COLS)}) "
    f"values ({', '.join('%(' + c + ')s' for c in _FIXTURE_COLS)}) "
    "on conflict (api_fixture_id) do update set "
    + ", ".join(f"{c} = excluded.{c}" for c in
                ("league_id", "season", "round", "kickoff_at", "home_team_id", "away_team_id", "elapsed"))
    + ", finished_at = coalesce(fixtures.finished_at, excluded.finished_at), "
    + ", ".join(f"{c} = case when fixtures.manual_score then fixtures.{c} else excluded.{c} end" for c in _SCORE_COLS)
)


def upsert_fixtures(conn: psycopg.Connection, rows: list[FixtureRow], now: datetime) -> tuple[dict[int, int], list[str]]:
    """Store fixtures in allowlisted leagues. Returns (api_fixture_id -> fixture id, notes on bad scores)."""
    leagues = active_leagues(conn)
    keep = [r for r in rows if r.api_league_id in leagues]
    if not keep:
        return {}, []
    teams = upsert_teams(conn, [r.home for r in keep] + [r.away for r in keep])
    params, notes = [], []
    for r in keep:
        issues = fixture_issues(r)
        if issues:
            notes.append(f"fixture {r.api_fixture_id} ({r.home.name} v {r.away.name}): {'; '.join(issues)}")
        params.append({
            "api_fixture_id": r.api_fixture_id, "league_id": leagues[r.api_league_id], "season": r.season,
            "round": r.round, "kickoff_at": r.kickoff_at,
            "home_team_id": teams[r.home.api_team_id], "away_team_id": teams[r.away.api_team_id],
            "elapsed": r.elapsed,
            # The API gives no full-time timestamp; kick-off + 2h is close enough for the 72h statistics rule.
            "finished_at": min(now, r.kickoff_at + timedelta(hours=2)) if r.status in FINAL else None,
            "status": r.status, "home_ht": r.home_ht, "away_ht": r.away_ht, "home_90": r.home_90,
            "away_90": r.away_90, "home_et": r.home_et, "away_et": r.away_et, "home_pen": r.home_pen,
            "away_pen": r.away_pen, "score_issue": "; ".join(issues) or None,
        })
    with conn.cursor() as cur:
        cur.executemany(_UPSERT_FIXTURE, params)
    ids = dict(conn.execute("select api_fixture_id, id from public.fixtures where api_fixture_id = any(%s)",
                            ([r.api_fixture_id for r in keep],)).fetchall())
    return ids, notes


_STAT_COLS = ("corners", "yellow_cards", "red_cards", "shots_total", "shots_on_target", "fouls", "possession", "xg")


def upsert_team_stats(conn: psycopg.Connection, fixture_id: int, stats: list[TeamStatsRow]) -> int:
    """Store both teams' statistics for a fixture and mark it fetched. Needs exactly two teams."""
    if len(stats) != 2:
        return 0
    teams = dict(conn.execute("select api_team_id, id from public.teams where api_team_id = any(%s)",
                              ([s.api_team_id for s in stats],)).fetchall())
    if len(teams) != 2:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            f"""insert into public.fixture_team_stats (fixture_id, team_id, {', '.join(_STAT_COLS)}, raw)
                values (%s, %s, {', '.join(['%s'] * len(_STAT_COLS))}, %s)
                on conflict (fixture_id, team_id) do update set
                {', '.join(f'{c} = excluded.{c}' for c in _STAT_COLS)}, raw = excluded.raw""",
            [(fixture_id, teams[s.api_team_id], *(getattr(s, c) for c in _STAT_COLS), Jsonb(s.raw)) for s in stats],
        )
    conn.execute("update public.fixtures set stats_fetched_at = now() where id = %s", (fixture_id,))
    return 2


def grade_all(conn: psycopg.Connection) -> dict:
    return conn.execute("select public.grade_all()").fetchone()[0]
