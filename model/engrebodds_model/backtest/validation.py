"""Rolling season-by-season validation: choose settings on earlier seasons, judge them on the next season.

For each validation season V (2020/21, 2021/22, 2022/23), an experiment's settings are chosen on the seasons
scored before V (from 2017/18) and evaluated on V. An experiment is accepted only if it beats the current
model on average across the three seasons and in at least two of them. Comparisons use the same matches
(those both versions predict with data quality >= 0.6). Nothing is accepted in-sample.
"""

import hashlib
import math
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd

from engrebodds_model.backtest import football_data as fd
from engrebodds_model.backtest.metrics import log_loss
from engrebodds_model.backtest.walkforward import Config, outcome_1x2, walk_forward_all
from engrebodds_model.calibration import Calibrator, fit_draw_shift, fit_map
from engrebodds_model.markets import OU_LINES, TEAM_LINES

VALIDATION = ("2020/21", "2021/22", "2022/23")
FIRST_SCORED = "2017/18"
PRED_RANGE = ("2017-07-01", "2023-07-01")
CACHE = fd.REPO_ROOT / "ingest" / "data" / "cache"
KEY = ["league", "date", "home", "away"]
MIN_QUALITY = 0.6


def cached_predictions(leagues: dict, cfg: Config, start: str, end: str, workers=None) -> pd.DataFrame:
    """Walk-forward predictions, cached on disk (git-ignored) by config, date range and league set."""
    tag = hashlib.sha1(f"{cfg.key()}|{start}|{end}|{sorted(leagues)}".encode()).hexdigest()[:16]
    path = CACHE / f"pred-{tag}.pkl"
    if path.exists():
        return pd.read_pickle(path)
    pred = walk_forward_all(leagues, start, end, cfg, workers)
    CACHE.mkdir(parents=True, exist_ok=True)
    pred.to_pickle(path)
    return pred


def keyed(df: pd.DataFrame) -> pd.Series:
    return df["league"] + "|" + df["date"].astype(str) + "|" + df["home"] + "|" + df["away"]


def common_keys(frames: list[pd.DataFrame]) -> set:
    sets = [set(keyed(f[f.dq >= MIN_QUALITY])) for f in frames]
    return set.intersection(*sets) if sets else set()


def restrict(df: pd.DataFrame, keys: set) -> pd.DataFrame:
    return df[keyed(df).isin(keys)]


# ---------- scores ----------

def ll_1x2(df: pd.DataFrame) -> float:
    return log_loss(df[["p_h", "p_d", "p_a"]].to_numpy(), outcome_1x2(df.hg, df.ag))


def goal_targets(df: pd.DataFrame) -> dict[str, tuple[str, np.ndarray]]:
    """Calibration key -> (probability column, whether it happened), for every two-way goals market."""
    total = (df.hg + df.ag).to_numpy()
    out = {f"OU|O|{l:g}": (f"p_o{l:g}", total > l) for l in OU_LINES}
    out["BTTS|Y"] = ("p_btts", ((df.hg > 0) & (df.ag > 0)).to_numpy())
    for l in TEAM_LINES:
        out[f"TEAM_OU|HO|{l:g}"] = (f"p_ho{l:g}", df.hg.to_numpy() > l)
        out[f"TEAM_OU|AO|{l:g}"] = (f"p_ao{l:g}", df.ag.to_numpy() > l)
    return out


def _binary_ll(p, hit) -> float:
    p = np.clip(np.asarray(p, dtype=float), 1e-12, 1 - 1e-12)
    return float(-np.mean(np.where(hit, np.log(p), np.log(1 - p))))


def ll_goals(df: pd.DataFrame, calibrator: Calibrator | None = None) -> float:
    """Mean binary log loss across the goals markets (each market weighted equally)."""
    losses = []
    for key, (col, hit) in goal_targets(df).items():
        p = df[col].to_numpy()
        if calibrator and key in calibrator.maps:
            p = calibrator.maps[key].apply(p)
        losses.append(_binary_ll(p, hit))
    return float(np.mean(losses))


# ---------- rolling selection and comparison ----------

def _seasons_before(df: pd.DataFrame, season: str, first: str = FIRST_SCORED) -> pd.DataFrame:
    return df[(df.season >= first) & (df.season < season)]


def rolling(preds: dict[str, pd.DataFrame], keys: set, score: Callable[[pd.DataFrame], float],
            seasons=VALIDATION, first: str = FIRST_SCORED) -> list[dict]:
    """Per validation season: the config chosen on earlier seasons, and its score on that season."""
    out = []
    for season in seasons:
        train = {k: score(_seasons_before(restrict(p, keys), season, first)) for k, p in preds.items()}
        chosen = min(train, key=train.get)
        test = restrict(preds[chosen], keys)
        out.append({"season": season, "chosen": chosen, "score": score(test[test.season == season])})
    return out


def compare(base: list[dict], variant: list[dict], threshold: float = 0.0) -> dict:
    """Accept if the variant's score is lower on average (by more than threshold) and in at least two thirds
    of the validation seasons (2 of 3; both of 2)."""
    diffs = [v["score"] - b["score"] for b, v in zip(base, variant)]
    need = math.ceil(2 * len(diffs) / 3)
    return {"diffs": diffs, "mean": float(np.mean(diffs)), "wins": int(sum(d < 0 for d in diffs)),
            "accepted": bool(np.mean(diffs) < -threshold and sum(d < 0 for d in diffs) >= need),
            "base": base, "variant": variant}


def fit_calibrator(train: pd.DataFrame, draw: bool = True) -> Calibrator:
    """Goals-market maps (and a draw shift) fitted on the given (earlier) predictions only."""
    maps = {key: fit_map(train[col], hit) for key, (col, hit) in goal_targets(train).items()}
    shift = fit_draw_shift(train[["p_h", "p_d", "p_a"]].to_numpy(), outcome_1x2(train.hg, train.ag)) if draw else 0.0
    return Calibrator(maps, shift)


def rolling_calibration(chosen: dict[str, pd.DataFrame], keys: set, seasons=VALIDATION, first: str = FIRST_SCORED) -> dict:
    """Calibration and draw shift fitted on seasons before V, applied to V. chosen: season -> predictions."""
    goals, draw = [], []
    for season in seasons:
        df = restrict(chosen[season], keys)
        train, test = _seasons_before(df, season, first), df[df.season == season]
        cal = fit_calibrator(train)
        y = outcome_1x2(test.hg, test.ag)
        p3 = test[["p_h", "p_d", "p_a"]].to_numpy()
        from engrebodds_model.calibration import shift_draw
        goals.append({"season": season, "base": ll_goals(test), "calibrated": ll_goals(test, cal)})
        draw.append({"season": season, "base": log_loss(p3, y), "calibrated": log_loss(shift_draw(p3, cal.draw_shift), y),
                     "shift": cal.draw_shift})
    as_rows = lambda rows, k: [{"season": r["season"], "score": r[k]} for r in rows]
    return {"goals": compare(as_rows(goals, "base"), as_rows(goals, "calibrated")),
            "draw": compare(as_rows(draw, "base"), as_rows(draw, "calibrated")), "draw_shifts": [d["shift"] for d in draw]}


def rolling_per_league(preds: dict[str, pd.DataFrame], keys: set) -> list[dict]:
    """Per validation season: each league picks its own config on earlier seasons; pooled 1X2 log loss on V."""
    out = []
    for season in VALIDATION:
        parts = []
        leagues = sorted(set(next(iter(preds.values())).league))
        for code in leagues:
            train = {k: _seasons_before(restrict(p[p.league == code], keys), season) for k, p in preds.items()}
            scores = {k: ll_1x2(t) for k, t in train.items() if len(t) >= 100}
            if not scores:
                continue
            chosen = min(scores, key=scores.get)
            test = restrict(preds[chosen][preds[chosen].league == code], keys)
            parts.append(test[test.season == season])
        out.append({"season": season, "chosen": "per league", "score": ll_1x2(pd.concat(parts))})
    return out
