"""Ingestion jobs. Each job module exposes `run(settings, **options) -> None` and is registered in JOBS."""

from collections.abc import Callable

from engrebodds_ingest.jobs import (
    audit, sync_fixtures, sync_odds, sync_reference, sync_results, sync_standings, sync_stats,
)

JOBS: dict[str, Callable[..., None]] = {
    "audit": audit.run,
    "sync_reference": sync_reference.run,
    "sync_fixtures": sync_fixtures.run,
    "sync_results": sync_results.run,
    "sync_odds": sync_odds.run,
    "sync_stats": sync_stats.run,
    "sync_standings": sync_standings.run,
}
