"""Walk-forward predictions: refit every week on matches strictly before the cutoff, predict the next week.

Nothing on or after a cutoff date is ever in that cutoff's training data (tested in test_backtest.py).
Each prediction row keeps the expected goals (lam, mu), rho and half-time share, so products can rebuild
the score matrices, plus the headline probabilities used for scoring.
"""

from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

import numpy as np
import pandas as pd

from engrebodds_model.dixon_coles import decay_weights, fit, prepare, score_matrix
from engrebodds_model.markets import ht_matrix, prob

MIN_TRAIN_MATCHES = 50
QUALITY_MATCHES = 10          # data quality = min(1, fewer team's matches in the last year / 10)


@dataclass(frozen=True)
class Config:
    xi: float = 0.002                 # time decay per day
    penalty: float = 5.0              # ridge strength, in weighted matches
    window_days: int = 1095           # about three seasons of history
    refit_days: int = 7
    ht_share: float | None = None     # None: estimate per league from the training window


def ht_share_from(train: pd.DataFrame) -> float:
    ok = train.dropna(subset=["hthg", "htag"])
    total = (ok.hg + ok.ag).sum()
    return float((ok.hthg + ok.htag).sum() / total) if total else 0.44


def walk_forward(df: pd.DataFrame, start, end, cfg: Config) -> pd.DataFrame:
    """Predictions for one league's matches with start <= date < end."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    window, step = pd.Timedelta(days=cfg.window_days), pd.Timedelta(days=cfg.refit_days)
    rows = []
    cutoff = start
    while cutoff < end:
        nxt = min(cutoff + step, end)
        test = df[(df.date >= cutoff) & (df.date < nxt)]
        train = df[(df.date < cutoff) & (df.date >= cutoff - window)]
        if len(test) and len(train) >= MIN_TRAIN_MATCHES:
            days = (cutoff - train.date).dt.days.to_numpy()
            data, teams = prepare(train.home.tolist(), train.away.tolist(), train.hg, train.ag,
                                  decay_weights(days, cfg.xi))
            model = fit(data, teams, cfg.penalty)
            recent = train[train.date >= cutoff - pd.Timedelta(days=365)]
            counts = pd.concat([recent.home, recent.away]).value_counts()
            share = cfg.ht_share if cfg.ht_share is not None else ht_share_from(train)
            known = set(teams)
            for idx, r in test.iterrows():
                if r.home not in known or r.away not in known:
                    continue
                lam, mu = model.rates(r.home, r.away)
                ft = score_matrix(lam, mu, model.rho)
                ht = ht_matrix(lam, mu, share)
                rows.append({
                    "idx": idx, "cutoff": cutoff, "lam": lam, "mu": mu, "rho": model.rho, "ht_share": share,
                    "dq": min(1.0, min(counts.get(r.home, 0), counts.get(r.away, 0)) / QUALITY_MATCHES),
                    "p_h": prob(ft, "1X2", "1"), "p_d": prob(ft, "1X2", "X"), "p_a": prob(ft, "1X2", "2"),
                    "p_o25": prob(ft, "OU", "O", 2.5), "p_btts": prob(ft, "BTTS", "Y"),
                    "p_ht_h": prob(ht, "HT_1X2", "1"), "p_ht_d": prob(ht, "HT_1X2", "X"),
                    "p_ht_a": prob(ht, "HT_1X2", "2"),
                })
        cutoff = nxt
    if not rows:
        return pd.DataFrame()
    pred = pd.DataFrame(rows).set_index("idx")
    return df.loc[pred.index].join(pred)


def _one(args) -> pd.DataFrame:
    df, start, end, cfg = args
    return walk_forward(df, start, end, cfg)


def walk_forward_all(leagues: dict[str, pd.DataFrame], start, end, cfg: Config, workers: int | None = None) -> pd.DataFrame:
    """Walk-forward predictions for many leagues in parallel, concatenated."""
    jobs = [(df, start, end, cfg) for df in leagues.values() if not df.empty]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        parts = [p for p in pool.map(_one, jobs) if not p.empty]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def base_rates(df: pd.DataFrame, before) -> dict[str, float]:
    """League frequencies (home/draw/away, over 2.5) in the three years before a date: the naive benchmark."""
    before = pd.Timestamp(before)
    past = df[(df.date < before) & (df.date >= before - pd.Timedelta(days=1095))]
    n = max(len(past), 1)
    return {
        "h": float((past.hg > past.ag).sum() / n), "d": float((past.hg == past.ag).sum() / n),
        "a": float((past.hg < past.ag).sum() / n), "o25": float(((past.hg + past.ag) > 2).sum() / n),
    }


def outcome_1x2(hg, ag) -> np.ndarray:
    hg, ag = np.asarray(hg), np.asarray(ag)
    return np.where(hg > ag, 0, np.where(hg == ag, 1, 2))
