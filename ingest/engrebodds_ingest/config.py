"""Settings read from the environment (and a .env file at the repo root, if present)."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(REPO_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    api_key: str
    database_url: str
    quota_floor: int


def load_settings() -> Settings:
    return Settings(
        api_key=os.environ.get("API_FOOTBALL_KEY", ""),
        database_url=os.environ.get(
            "DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
        ),
        quota_floor=int(os.environ.get("API_QUOTA_FLOOR", "500")),
    )
