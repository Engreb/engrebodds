"""Walk-forward predictions: refit every week on matches strictly before the cutoff, predict the next week.

Nothing on or after a cutoff date is ever in that cutoff's training data (tested in test_backtest.py).
Options (Config):
- link_divisions: fit each country's divisions together (one rating scale, a goal level per division);
- new_team_prior: pull teams new to the group this season towards the bottom-three average, not 0;
- season_reset: multiply the weight of matches from earlier seasons by this factor (1 = off);
- shots_weight: blend expected goals with a shots-on-target rating model in log space (0 = off).
Each row keeps lam/mu/rho/half-time share so products can rebuild score matrices, plus the probabilities
used for scoring and calibration.
"""

from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

import numpy as np
import pandas as pd

from engrebodds_model.dixon_coles import decay_weights, fit, prepare, score_matrix
from engrebodds_model.markets import OU_LINES, TEAM_LINES, ht_matrix, prob

MIN_TRAIN_MATCHES = 50
QUALITY_MATCHES = 10          # data quality = min(1, fewer team's matches in the last year / 10)

PYRAMIDS = {
    "England": ("E0", "E1", "E2", "E3", "EC"), "Scotland": ("SC0", "SC1", "SC2", "SC3"),
    "Germany": ("D1", "D2"), "Italy": ("I1", "I2"), "Spain": ("SP1", "SP2"), "France": ("F1", "F2"),
}


@dataclass(frozen=True)
class Config:
    xi: float = 0.002                 # time decay per day
    penalty: float = 5.0              # ridge strength, in weighted matches
    window_days: int = 1095           # about three seasons of history
    refit_days: int = 7
    ht_share: float | None = None     # None: estimate per division from the training window
    link_divisions: bool = False
    new_team_prior: bool = False
    season_reset: float = 1.0
    shots_weight: float = 0.0

    def key(self) -> str:
        return (f"xi={self.xi} pen={self.penalty} link={int(self.link_divisions)} prior={int(self.new_team_prior)} "
                f"reset={self.season_reset} shots={self.shots_weight}")


def groups(leagues: dict[str, pd.DataFrame], link: bool) -> list[pd.DataFrame]:
    """One frame per fitting group: a country's linked divisions, or each league alone."""
    if not link:
        return [df for df in leagues.values() if not df.empty]
    used, out = set(), []
    for codes in PYRAMIDS.values():
        parts = [leagues[c] for c in codes if c in leagues and not leagues[c].empty]
        if parts:
            out.append(pd.concat(parts, ignore_index=True).sort_values(["date", "home"]).reset_index(drop=True))
            used.update(codes)
    out += [df for code, df in leagues.items() if code not in used and not df.empty]
    return out


def ht_share_from(train: pd.DataFrame) -> float:
    ok = train.dropna(subset=["hthg", "htag"])
    total = (ok.hg + ok.ag).sum()
    return float((ok.hthg + ok.htag).sum() / total) if total else 0.44


def _weights(train: pd.DataFrame, cutoff, current_season: str, cfg: Config) -> np.ndarray:
    w = decay_weights((cutoff - train.date).dt.days.to_numpy(), cfg.xi)
    if cfg.season_reset < 1.0:
        w = np.where(train.season.to_numpy() == current_season, w, w * cfg.season_reset)
    return w


def _new_team_priors(train: pd.DataFrame, current_season: str, teams: list, model) -> tuple:
    """Bottom-three average ratings per division, for teams with no group matches before this season."""
    before = train[train.season != current_season]
    known = set(before.home) | set(before.away)
    new = [t for t in teams if t not in known]
    if not new or before.empty:
        return None, None
    idx = {t: i for i, t in enumerate(teams)}
    strength = model.attack - model.defence
    pa, pd_ = np.zeros(len(teams)), np.zeros(len(teams))
    last_div = pd.concat([train[["date", "home", "league"]].rename(columns={"home": "team"}),
                          train[["date", "away", "league"]].rename(columns={"away": "team"})]
                         ).sort_values("date").groupby("team")["league"].last()
    for t in new:
        div = last_div.get(t)
        peers = [p for p in set(before[before.league == div].home) if p in idx]
        if len(peers) < 3:
            continue
        bottom = sorted(peers, key=lambda p: strength[idx[p]])[:3]
        pa[idx[t]] = np.mean([model.attack[idx[p]] for p in bottom])
        pd_[idx[t]] = np.mean([model.defence[idx[p]] for p in bottom])
    return pa, pd_


def walk_forward(df: pd.DataFrame, start, end, cfg: Config) -> pd.DataFrame:
    """Predictions for one group's matches with start <= date < end."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    window, step = pd.Timedelta(days=cfg.window_days), pd.Timedelta(days=cfg.refit_days)
    has_shots = cfg.shots_weight > 0 and {"hst", "ast"} <= set(df.columns)
    rows = []
    cutoff = start
    while cutoff < end:
        nxt = min(cutoff + step, end)
        test = df[(df.date >= cutoff) & (df.date < nxt)]
        train = df[(df.date < cutoff) & (df.date >= cutoff - window)]
        if len(test) and len(train) >= MIN_TRAIN_MATCHES:
            current = test.season.iloc[0]
            w = _weights(train, cutoff, current, cfg)
            divs = train.league.tolist() if cfg.link_divisions else None
            data, teams = prepare(train.home.tolist(), train.away.tolist(), train.hg, train.ag, w, divs)
            div_labels = sorted(set(divs)) if divs else [None]
            model = fit(data, teams, cfg.penalty)
            if cfg.new_team_prior:
                pa, pd_ = _new_team_priors(train, current, teams, model)
                if pa is not None:
                    model = fit(data, teams, cfg.penalty, pa, pd_)

            shots_model, conversion = None, {}
            if has_shots:
                st = train.dropna(subset=["hst", "ast"])
                if len(st) >= MIN_TRAIN_MATCHES:
                    sdivs = st.league.tolist() if cfg.link_divisions else None
                    sdata, steams = prepare(st.home.tolist(), st.away.tolist(), st.hst, st.ast,
                                            _weights(st, cutoff, current, cfg), sdivs)
                    shots_model = fit(sdata, steams, cfg.penalty, fix_rho=True)
                    for k, label in enumerate(sorted(set(sdivs)) if sdivs else [None]):
                        part = st if label is None else st[st.league == label]
                        conversion[k] = (part.hg + part.ag).sum() / max((part.hst + part.ast).sum(), 1)

            recent = train[train.date >= cutoff - pd.Timedelta(days=365)]
            counts = pd.concat([recent.home, recent.away]).value_counts()
            shares = {label: (cfg.ht_share if cfg.ht_share is not None
                              else ht_share_from(train if label is None else train[train.league == label]))
                      for label in div_labels}
            known = set(teams)
            for idx, r in test.iterrows():
                if r.home not in known or r.away not in known:
                    continue
                label = r.league if cfg.link_divisions else None
                if label not in div_labels:
                    continue
                k = div_labels.index(label)
                lam, mu = model.rates(r.home, r.away, k)
                if shots_model is not None and r.home in shots_model.teams and r.away in shots_model.teams:
                    sl, sm = shots_model.rates(r.home, r.away, k)
                    s = cfg.shots_weight
                    lam = float(np.exp((1 - s) * np.log(lam) + s * np.log(conversion[k] * sl)))
                    mu = float(np.exp((1 - s) * np.log(mu) + s * np.log(conversion[k] * sm)))
                ft = score_matrix(lam, mu, model.rho)
                ht = ht_matrix(lam, mu, shares[label])
                row = {"idx": idx, "cutoff": cutoff, "lam": lam, "mu": mu, "rho": model.rho,
                       "ht_share": shares[label],
                       "dq": min(1.0, min(counts.get(r.home, 0), counts.get(r.away, 0)) / QUALITY_MATCHES),
                       "p_h": prob(ft, "1X2", "1"), "p_d": prob(ft, "1X2", "X"), "p_a": prob(ft, "1X2", "2"),
                       "p_btts": prob(ft, "BTTS", "Y"),
                       "p_ht_h": prob(ht, "HT_1X2", "1"), "p_ht_d": prob(ht, "HT_1X2", "X"),
                       "p_ht_a": prob(ht, "HT_1X2", "2")}
                for line in OU_LINES:
                    row[f"p_o{line:g}"] = prob(ft, "OU", "O", line)
                for line in TEAM_LINES:
                    row[f"p_ho{line:g}"] = prob(ft, "TEAM_OU", "HO", line)
                    row[f"p_ao{line:g}"] = prob(ft, "TEAM_OU", "AO", line)
                rows.append(row)
        cutoff = nxt
    if not rows:
        return pd.DataFrame()
    pred = pd.DataFrame(rows).set_index("idx")
    out = df.loc[pred.index].join(pred)
    out["p_o25"] = out["p_o2.5"]            # name used by the first backtest
    return out


def _one(args) -> pd.DataFrame:
    df, start, end, cfg = args
    return walk_forward(df, start, end, cfg)


def walk_forward_all(leagues: dict[str, pd.DataFrame], start, end, cfg: Config, workers: int | None = None) -> pd.DataFrame:
    """Walk-forward predictions for all leagues (grouped as the config says), in parallel, concatenated."""
    jobs = [(g, start, end, cfg) for g in groups(leagues, cfg.link_divisions)]
    if workers == 0:                      # sequential (e.g. when run from an inline script on Windows)
        parts = [p for p in map(_one, jobs) if not p.empty]
    else:
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
