"""Twice daily: fixtures by date for every allowlisted league (one request per date)."""

from datetime import UTC, datetime, timedelta

from engrebodds_ingest.api import ApiError
from engrebodds_ingest.config import Settings
from engrebodds_ingest.db import job_run, upsert_fixtures
from engrebodds_ingest.normalise import fixture_row


def run(settings: Settings, days_back: int = 1, days_ahead: int = 3, **_) -> None:
    with job_run(settings, "sync_fixtures") as ctx:
        now = datetime.now(UTC)
        for offset in range(-days_back, days_ahead + 1):
            day = (now + timedelta(days=offset)).date().isoformat()
            try:
                items = ctx.api.get_all("/fixtures", date=day)
            except ApiError as e:   # e.g. the free plan only allows yesterday to tomorrow
                ctx.notes.append(f"{day}: {e.errors}")
                continue
            ids, notes = upsert_fixtures(ctx.conn, [fixture_row(x) for x in items], now)
            ctx.rows += len(ids)
            ctx.notes += notes
            ctx.notes.append(f"{day}: {len(items)} fixtures listed, {len(ids)} on the allowlist")
