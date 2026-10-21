"""Ingestion jobs. Each job module exposes `run(settings) -> None` and is registered in JOBS."""

from collections.abc import Callable

from engrebodds_ingest.config import Settings
from engrebodds_ingest.jobs import audit

JOBS: dict[str, Callable[[Settings], None]] = {
    "audit": audit.run,
}
