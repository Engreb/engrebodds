"""Daily predictions for upcoming fixtures (Phase 2 step 3, docs/phase-2-step-3-plan.md).

For each allowlisted domestic league: fit on its finished matches (last ~3 years, from our own database,
zero API requests) with the shared backtest code (walkforward.fit_group), then predict fixtures in the next
48 hours that are more than 1 hour from kick-off. Domestic cup ties are predicted only when both teams are
rated in the same domestic league; mixed ties and continental competitions ("World") are skipped and noted.

Per market we store p_model (calibrated if the active settings say so) in `probs`, and for 1X2 and O/U 2.5
a market-based p_final in `final`, from the latest 1xBet snapshot with the margin removed by the power method.
Any key not in `final` has p_final = p_model.
"""

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
from psycopg.types.json import Jsonb

from engrebodds_ingest.config import Settings
from engrebodds_ingest.db import job_run
from engrebodds_model.backtest.walkforward import fit_group
from engrebodds_model.dixon_coles import score_matrix
from engrebodds_model.implied import demargin_power
from engrebodds_model.live import data, settings as live_settings
from engrebodds_model.live.settings import LiveSettings
from engrebodds_model.markets import all_markets, ht_matrix, key, top_scores

HORIZON_HOURS = 48
MARKET_FINAL = {"1X2": (("1", "X", "2"), None), "OU": (("O", "U"), 2.5)}   # markets that use the market price


def market_final(odds: dict | None) -> dict:
    """{key: {p, bookmaker, captured_at}} for 1X2 and O/U 2.5 when the display bookmaker priced every selection."""
    final = {}
    if not odds:
        return final
    for market, (selections, line) in MARKET_FINAL.items():
        quotes = [odds.get((market, s, line)) for s in selections]
        if any(q is None for q in quotes):
            continue
        probs = demargin_power([q[0] for q in quotes])
        if not np.all(np.isfinite(probs)):
            continue
        for s, p, (_, captured_at, bookmaker) in zip(selections, probs, quotes):
            final[key(market, s, line)] = {"p": round(float(p), 5), "bookmaker": bookmaker,
                                           "captured_at": pd.Timestamp(captured_at).isoformat()}
    return final


def build(train: pd.DataFrame, upcoming: pd.DataFrame, odds: dict[int, dict], ls: LiveSettings, now) -> tuple[list, list, dict]:
    """Predictions rows, model_runs rows and run notes. Pure: no database access."""
    now = pd.Timestamp(now)
    cfg = ls.config
    notes = {"predicted": 0, "market_final": 0, "skipped_world": 0, "skipped_mixed_cup": 0,
             "skipped_unrated": 0, "skipped_league_history": 0, "mixed_cup_examples": []}
    link = cfg.link_divisions
    fitted, meta = {}, {}
    for gkey, g in train.groupby("country" if link else "league"):
        g = g.sort_values(["date", "home"]).reset_index(drop=True)
        group = fit_group(g, now, g.season.iloc[-1], cfg)
        if group is not None:
            fitted[gkey] = group
            meta[gkey] = (g.country.iloc[0], g.date.min(), g.date.max(), len(g), sorted(set(g.league)))

    rows = []
    for f in upcoming.itertuples():
        if f.country == "World":
            notes["skipped_world"] += 1
            continue
        candidates = []
        if f.type == "League":
            gkey = f.country if link else f.league
            if gkey in fitted:
                candidates = [(fitted[gkey], f.league if link else None)]
            else:
                notes["skipped_league_history"] += 1
                continue
        else:    # domestic cup: only when both teams are rated in the same domestic league
            for gkey, group in fitted.items():
                country, *_, members = meta[gkey]
                if country != f.country:
                    continue
                for label in (members if link else [None]):
                    if group.knows(f.home, f.away, label):
                        candidates.append((group, label))
            if not candidates:
                notes["skipped_mixed_cup"] += 1
                if len(notes["mixed_cup_examples"]) < 5:
                    notes["mixed_cup_examples"].append(f"{f.league_name} fixture {f.fixture_id}")
                continue
        group, label = candidates[0]
        if not group.knows(f.home, f.away, label):
            notes["skipped_unrated"] += 1
            continue
        lam, mu = group.rates(f.home, f.away, label)
        rho = group.model.rho
        ft, ht = score_matrix(lam, mu, rho), ht_matrix(lam, mu, group.shares[label])
        probs = {k: round(float(v), 5) for k, v in ls.calibrator.apply(all_markets(ft, ht)).items()}
        final = market_final(odds.get(f.fixture_id))
        notes["predicted"] += 1
        notes["market_final"] += bool(final)
        rows.append({
            "fixture_id": int(f.fixture_id), "model_version": ls.version, "settings_id": ls.id,
            "lambda_home": round(lam, 3), "lambda_away": round(mu, 3), "rho": round(float(rho), 4),
            "p_home": probs["1X2|1"], "p_draw": probs["1X2|X"], "p_away": probs["1X2|2"],
            "probs": probs, "final": final,
            "cs_top": [{"score": s, "p": round(p, 4)} for s, p in top_scores(ft)],
            "ht_score": top_scores(ht, 1)[0][0],
            "data_quality": round(group.quality(f.home, f.away), 3),
            "fresh_days": group.freshness(f.home, f.away, now),
        })

    runs = []
    for gkey, group in fitted.items():
        country, start, end, n, members = meta[gkey]
        m = group.model
        params = {"home_adv": m.home_adv, "rho": m.rho, "bases": [float(b) for b in m.bases],
                  "ratings": {str(t): [round(float(a), 4), round(float(d), 4)] for t, a, d in zip(m.teams, m.attack, m.defence)}}
        for league in members:
            runs.append({"league_id": int(league), "train_from": start, "train_to": end, "n_matches": n, "params": params})
    return rows, runs, notes


UPSERT_PREDICTION = """
insert into public.predictions (fixture_id, model_version, settings_id, lambda_home, lambda_away, rho, p_home, p_draw,
                                p_away, probs, final, cs_top, ht_score, data_quality, fresh_days, computed_at)
values (%(fixture_id)s, %(model_version)s, %(settings_id)s, %(lambda_home)s, %(lambda_away)s, %(rho)s, %(p_home)s,
        %(p_draw)s, %(p_away)s, %(probs)s, %(final)s, %(cs_top)s, %(ht_score)s, %(data_quality)s, %(fresh_days)s, now())
on conflict (fixture_id, model_version) do update set
  settings_id = excluded.settings_id, lambda_home = excluded.lambda_home, lambda_away = excluded.lambda_away,
  rho = excluded.rho,
  p_home = excluded.p_home, p_draw = excluded.p_draw, p_away = excluded.p_away, probs = excluded.probs,
  final = excluded.final, cs_top = excluded.cs_top, ht_score = excluded.ht_score,
  data_quality = excluded.data_quality, fresh_days = excluded.fresh_days, computed_at = excluded.computed_at
"""

UPSERT_RUN = """
insert into public.model_runs (league_id, model_version, settings_id, fitted_at, train_from, train_to, n_matches, params)
values (%(league_id)s, %(model_version)s, %(settings_id)s, now(), %(train_from)s, %(train_to)s, %(n_matches)s, %(params)s)
on conflict (league_id, model_version) do update set
  settings_id = excluded.settings_id, fitted_at = excluded.fitted_at, train_from = excluded.train_from,
  train_to = excluded.train_to, n_matches = excluded.n_matches, params = excluded.params
"""


def write(conn, rows: list[dict], runs: list[dict], ls: LiveSettings) -> int:
    with conn.cursor() as cur:
        cur.executemany(UPSERT_PREDICTION, [{**r, "probs": Jsonb(r["probs"]), "final": Jsonb(r["final"]),
                                             "cs_top": Jsonb(r["cs_top"])} for r in rows])
        cur.executemany(UPSERT_RUN, [{**r, "model_version": ls.version, "settings_id": ls.id,
                                      "params": Jsonb(r["params"])} for r in runs])
    return conn.execute("select public.thin_predictions()").fetchone()[0]


def run(settings: Settings, **_) -> None:
    with job_run(settings, "predict") as ctx:
        ls = live_settings.active(ctx.conn)
        now = datetime.now(UTC).replace(tzinfo=None)
        train = data.training(ctx.conn, now - timedelta(days=ls.config.window_days), now)
        upcoming = data.upcoming(ctx.conn, HORIZON_HOURS)
        odds = data.display_odds(ctx.conn, upcoming.fixture_id.astype(int).tolist() if len(upcoming) else [])
        rows, runs, notes = build(train, upcoming, odds, ls, now)
        thinned = write(ctx.conn, rows, runs, ls)
        ctx.rows += len(rows)
        ctx.notes.append(f"settings {ls.version}: {ls.source}")
        ctx.notes.append(f"{len(upcoming)} upcoming fixtures; " + ", ".join(
            f"{k} {v}" for k, v in notes.items() if k != "mixed_cup_examples"))
        if notes["mixed_cup_examples"]:
            ctx.notes.append("mixed cup ties skipped, e.g. " + "; ".join(notes["mixed_cup_examples"]))
        ctx.notes.append(f"{len(runs)} model_runs rows; {thinned} old predictions thinned")
