"""Tune the live model's settings on API-Football results, the licensed data, never on football-data.

football-data.co.uk is private-use only, so nothing tuned on it may go into the live model. This loads
finished fixtures for allowlisted domestic leagues from Supabase, runs the same rolling season-by-season
validation as the research backtest (an experiment is accepted only if it wins out of sample), and writes
the chosen settings to model/live_settings.json.

Run once the history backfill has loaded enough seasons:
    python -m engrebodds_model.tune_live            (uses DATABASE_URL from .env)
"""

import json
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from engrebodds_model.backtest import validation as val
from engrebodds_model.backtest.walkforward import Config, walk_forward

SETTINGS_FILE = Path(__file__).resolve().parents[1] / "live_settings.json"
GRID = [(xi, pen) for xi in (0.0015, 0.003) for pen in (1.0, 3.0, 8.0)]
MIN_SEASONS = 3          # need at least one training season before two validation seasons

FIXTURES_SQL = """
select l.api_league_id::text as league, l.country, f.season::text as season,
       (f.kickoff_at at time zone 'UTC') as date, ht.name as home, aw.name as away,
       f.home_90 as hg, f.away_90 as ag, f.home_ht as hthg, f.away_ht as htag,
       sh.shots_on_target as hst, sa.shots_on_target as ast
  from public.fixtures f
  join public.leagues l on l.id = f.league_id
  join public.teams ht on ht.id = f.home_team_id
  join public.teams aw on aw.id = f.away_team_id
  left join public.fixture_team_stats sh on sh.fixture_id = f.id and sh.team_id = f.home_team_id
  left join public.fixture_team_stats sa on sa.fixture_id = f.id and sa.team_id = f.away_team_id
 where f.status in ('FT', 'AET', 'PEN') and f.home_90 is not null
   and l.tier <> 'off' and l.type = 'League'
 order by f.kickoff_at
"""


def load(conn) -> pd.DataFrame:
    df = pd.read_sql(FIXTURES_SQL, conn)
    df["date"] = pd.to_datetime(df["date"])
    return df


def groups(df: pd.DataFrame, link: bool) -> list[pd.DataFrame]:
    """A country's domestic leagues together when linking, otherwise each league alone."""
    by = "country" if link else "league"
    return [g.sort_values(["date", "home"]).reset_index(drop=True) for _, g in df.groupby(by)]


def predict(df: pd.DataFrame, cfg: Config, start, end) -> pd.DataFrame:
    parts = [walk_forward(g, start, end, cfg) for g in groups(df, cfg.link_divisions)]
    parts = [p for p in parts if not p.empty]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def tune(df: pd.DataFrame, grid=GRID, shots: bool = True) -> dict:
    """Rolling validation over the experiment ladder on API-Football data. Returns the live settings."""
    seasons = sorted(df.season.unique())
    if len(seasons) < MIN_SEASONS:
        raise ValueError(f"need at least {MIN_SEASONS} seasons of results, have {seasons}")
    first, validate = seasons[1], tuple(seasons[-2:])
    start, end = df[df.season == first].date.min(), df.date.max() + pd.Timedelta(days=1)

    def family(**kw):
        return {c.key(): c for c in (Config(xi=xi, penalty=p, **kw) for xi, p in grid)}

    def run(cfgs):
        return {k: predict(df, c, start, end) for k, c in cfgs.items()}

    def step(current, cand):
        keys = val.common_keys(list(current.values()) + list(cand.values()))
        return val.compare(val.rolling(current, keys, val.ll_1x2, validate, first),
                           val.rolling(cand, keys, val.ll_1x2, validate, first))

    configs, ladder = {}, []
    current, kw = run(family()), {}
    configs.update(family())
    cand_cfgs = family(link_divisions=True, new_team_prior=True)
    cand = run(cand_cfgs)
    configs.update(cand_cfgs)
    ladder.append({"experiment": "linked divisions + new-team prior", **_brief(step(current, cand))})
    if ladder[-1]["accepted"]:
        current, kw = cand, {"link_divisions": True, "new_team_prior": True}

    cand_cfgs = {**family(**kw, season_reset=0.5), **family(**kw, season_reset=0.8)}
    cand = run(cand_cfgs)
    configs.update(cand_cfgs)
    ladder.append({"experiment": "season-boundary reset", **_brief(step(current, cand))})
    if ladder[-1]["accepted"]:
        current = cand

    has_shots = df[["hst", "ast"]].notna().all(axis=1).mean() > 0.8
    if shots and has_shots:
        best = _best(current, first)
        cand_cfgs = {**family(**kw, season_reset=configs[best].season_reset, shots_weight=0.3),
                     **family(**kw, season_reset=configs[best].season_reset, shots_weight=0.5)}
        cand = run(cand_cfgs)
        configs.update(cand_cfgs)
        ladder.append({"experiment": "shots on target", **_brief(step(current, cand))})
        if ladder[-1]["accepted"]:
            current = cand
    else:
        ladder.append({"experiment": "shots on target", "skipped": "not enough shots data (needs the Pro statistics backfill)"})

    keys = val.common_keys(list(current.values()))
    chosen = {r["season"]: current[r["chosen"]] for r in val.rolling(current, keys, val.ll_1x2, validate, first)}
    cal = val.rolling_calibration(chosen, keys, validate, first)
    ladder.append({"experiment": "goals calibration", **_brief(cal["goals"])})
    ladder.append({"experiment": "draw shift", **_brief(cal["draw"])})

    best = _best(current, first)
    final = configs[best]
    pre = current[best]
    pre = pre[(pre.dq >= val.MIN_QUALITY) & (pre.season >= first)]
    calibrator = val.fit_calibrator(pre, draw=cal["draw"]["accepted"])
    if not cal["goals"]["accepted"]:
        calibrator = replace(calibrator, maps={})
    return {"source": "API-Football (licensed); never football-data", "generated_at": datetime.now(UTC).isoformat(),
            "seasons": seasons, "validated_on": list(validate), "config": asdict(final),
            "calibrator": json.loads(calibrator.to_json()), "ladder": ladder}


def _best(preds: dict[str, pd.DataFrame], first: str) -> str:
    scores = {k: val.ll_1x2(p[(p.dq >= val.MIN_QUALITY) & (p.season >= first)]) for k, p in preds.items()}
    return min(scores, key=scores.get)


def _brief(res: dict) -> dict:
    return {"accepted": res["accepted"], "mean_change": res["mean"], "wins": res["wins"],
            "per_season": [float(d) for d in res["diffs"]]}


def main() -> None:
    import psycopg
    from engrebodds_ingest.config import load_settings
    with psycopg.connect(load_settings().database_url) as conn:
        df = load(conn)
    settings = tune(df)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    print(f"Wrote {SETTINGS_FILE} (validated on {settings['validated_on']})")


if __name__ == "__main__":
    main()
