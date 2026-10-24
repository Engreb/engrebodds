"""Live model settings: the active row in model_settings, or a-priori defaults.

Nothing tuned on football-data.co.uk may be used here (private-use data). Until tune_live has produced
settings from API-Football results and someone has activated them, predictions use defaults chosen in
advance (Dixon & Coles 1997 time decay, a light penalty, no shots, no calibration), versioned "dc-untuned".
"""

import json
from dataclasses import dataclass, fields
from datetime import UTC, datetime

import psycopg
from psycopg.types.json import Jsonb

from engrebodds_model.backtest.walkforward import Config
from engrebodds_model.calibration import Calibrator

DEFAULTS = {
    "version": "dc-untuned",
    "source": "a-priori defaults (Dixon & Coles 1997 decay 0.0019/day, penalty 1.0, no shots, no calibration); not tuned",
    "config": {"xi": 0.0019, "penalty": 1.0, "window_days": 1095},
    "calibrator": {"maps": {}, "draw_shift": 0.0},
}


@dataclass(frozen=True)
class LiveSettings:
    id: int | None
    version: str
    source: str
    config: Config
    calibrator: Calibrator


def from_parts(settings_id, version: str, source: str, config: dict, calibrator: dict) -> LiveSettings:
    known = {f.name for f in fields(Config)}
    return LiveSettings(settings_id, version, source,
                        Config(**{k: v for k, v in config.items() if k in known}),
                        Calibrator.from_json(json.dumps(calibrator)))


def defaults() -> LiveSettings:
    return from_parts(None, DEFAULTS["version"], DEFAULTS["source"], DEFAULTS["config"], DEFAULTS["calibrator"])


def active(conn: psycopg.Connection) -> LiveSettings:
    row = conn.execute("select id, version, source, config, calibrator from public.model_settings where is_active").fetchone()
    return from_parts(*row) if row else defaults()


def save(conn: psycopg.Connection, tuned: dict) -> int:
    """Store tune_live's result as a new, inactive settings row (activate it separately after review)."""
    n = conn.execute("select count(*) from public.model_settings").fetchone()[0]
    version = f"dc-{datetime.now(UTC):%Y%m%d}-{n + 1}"
    return conn.execute(
        """insert into public.model_settings (version, is_active, source, config, calibrator, validated_on, ladder)
           values (%s, false, %s, %s, %s, %s, %s) returning id""",
        (version, tuned["source"], Jsonb(tuned["config"]), Jsonb(tuned["calibrator"]),
         tuned.get("validated_on"), Jsonb(tuned.get("ladder"))),
    ).fetchone()[0]


def activate(conn: psycopg.Connection, settings_id: int) -> str:
    """Make one settings row the active one (exactly one active at a time). Returns its version."""
    row = conn.execute("select version from public.model_settings where id = %s", (settings_id,)).fetchone()
    if not row:
        raise ValueError(f"no model_settings row with id {settings_id}")
    conn.execute("update public.model_settings set is_active = false where is_active and id <> %s", (settings_id,))
    conn.execute("update public.model_settings set is_active = true, activated_at = now() where id = %s", (settings_id,))
    return row[0]
