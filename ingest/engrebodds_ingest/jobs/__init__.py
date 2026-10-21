"""Ingestion jobs. Each job module exposes `run(settings) -> None` and is registered in JOBS."""

from collections.abc import Callable

from engrebodds_ingest.config import Settings

# Filled in as jobs are built (audit, sync_reference, sync_fixtures, ...).
JOBS: dict[str, Callable[[Settings], None]] = {}
