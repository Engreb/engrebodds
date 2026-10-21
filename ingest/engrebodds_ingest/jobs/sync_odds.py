"""Four times a day: pre-match odds for today and tomorrow, stored under the lean policy.

Only prices that changed since the last snapshot are written, and only before kick-off.
"""

from datetime import UTC, datetime, timedelta

import psycopg

from engrebodds_ingest.api import ApiError
from engrebodds_ingest.config import Settings
from engrebodds_ingest.db import job_run
from engrebodds_ingest.normalise import OddsPolicy, odds_policy, odds_rows


def load_policy(conn: psycopg.Connection) -> tuple[OddsPolicy, dict[int, int]]:
    """The lean policy from the bookmakers table, and api_bookmaker_id -> bookmaker id."""
    rows = conn.execute(
        "select api_bookmaker_id, id, is_display from public.bookmakers where store_odds and api_bookmaker_id is not null"
    ).fetchall()
    display = next(api_id for api_id, _, is_display in rows if is_display)
    return odds_policy(display, {api_id for api_id, _, _ in rows}), {api_id: bid for api_id, bid, _ in rows}


def store_odds(conn: psycopg.Connection, items: list[dict], now: datetime) -> int:
    """Write changed prices for upcoming fixtures we hold. Returns rows inserted."""
    policy, bookmakers = load_policy(conn)
    api_ids = [item["fixture"]["id"] for item in items]
    fixtures = dict(conn.execute(
        "select api_fixture_id, id from public.fixtures where api_fixture_id = any(%s) and kickoff_at > %s",
        (api_ids, now)).fetchall())
    if not fixtures:
        return 0
    latest = {(f, b, m, s, line): price for f, b, m, s, line, price in conn.execute(
        """select fixture_id, bookmaker_id, market_code, selection, line, price
             from public.odds_latest where fixture_id = any(%s)""", (list(fixtures.values()),))}

    new = []
    for item in items:
        fid = fixtures.get(item["fixture"]["id"])
        if not fid:
            continue
        for r in odds_rows(item, policy):
            key = (fid, bookmakers[r.api_bookmaker_id], r.market_code, r.selection, r.line)
            if latest.get(key) != r.price:
                new.append((*key, r.price, now))
    with conn.cursor() as cur:
        cur.executemany(
            """insert into public.odds_snapshots (fixture_id, bookmaker_id, market_code, selection, line, price, captured_at)
               values (%s, %s, %s, %s, %s, %s, %s)""", new)
    return len(new)


def run(settings: Settings, days_ahead: int = 1, max_pages: int | None = None, **_) -> None:
    with job_run(settings, "sync_odds") as ctx:
        now = datetime.now(UTC)
        for offset in range(0, days_ahead + 1):
            day = (now + timedelta(days=offset)).date().isoformat()
            try:
                items = ctx.api.get_all("/odds", date=day, max_pages=max_pages)
            except ApiError as e:
                ctx.notes.append(f"{day}: {e.errors}")
                continue
            written = store_odds(ctx.conn, items, now)
            ctx.rows += written
            ctx.notes.append(f"{day}: odds for {len(items)} fixtures, {written} changed prices stored")
