"""Dixon-Coles (1997) goals model with time decay, a ridge penalty and linked divisions.

Expected goals for home team h against away team a, in a match of division k:
    lambda (home) = exp(base[k] + home_adv + attack[h] + defence[a])
    mu     (away) = exp(base[k] + attack[a] + defence[h])
`defence` is a weakness rating (higher concedes more). Attack and defence each sum to zero over all teams,
and each division has its own goal level `base[k]`. When a country's divisions are fitted together, teams
that moved between divisions put every team on one scale, so a promoted team arrives with a rating.
The ridge penalty pulls each team towards a prior rating (default 0 = average), which keeps teams with
few matches sensible. Low scores (0-0, 1-0, 0-1, 1-1) get the Dixon-Coles correction tau, controlled by
rho (fixed at 0 for the shots-on-target variant, which is plain Poisson).
Each match is weighted by exp(-xi * days_ago) (optionally scaled down for earlier seasons).
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson

RATING_BOUNDS = (-3.0, 3.0)
BASE_BOUNDS = (-2.0, 2.0)
HOME_BOUNDS = (-1.0, 1.5)
RHO_BOUNDS = (-0.25, 0.25)
MAX_GOALS = 10


def decay_weights(days_ago, xi: float) -> np.ndarray:
    """Match weights exp(-xi * days_ago); xi = 0 weights every match equally."""
    return np.exp(-xi * np.asarray(days_ago, dtype=float))


@dataclass(frozen=True)
class MatchData:
    """Matches as arrays, with teams as indices 0..n_teams-1 and divisions as 0..n_divisions-1."""
    home: np.ndarray
    away: np.ndarray
    home_goals: np.ndarray
    away_goals: np.ndarray
    weights: np.ndarray
    n_teams: int
    division: np.ndarray | None = None
    n_divisions: int = 1


def prepare(home_ids: Sequence, away_ids: Sequence, home_goals: Sequence, away_goals: Sequence,
            weights: Sequence | None = None, divisions: Sequence | None = None) -> tuple[MatchData, list]:
    """Index teams (and divisions) and pack matches. Returns the data and the team ids in index order.

    Divisions, when given, are indexed in sorted order of their labels; MatchData.division holds the index.
    """
    teams = sorted(set(home_ids) | set(away_ids))
    index = {t: i for i, t in enumerate(teams)}
    n = len(home_ids)
    if divisions is None:
        div, n_div = np.zeros(n, dtype=int), 1
    else:
        labels = sorted(set(divisions))
        div_index = {d: i for i, d in enumerate(labels)}
        div, n_div = np.array([div_index[d] for d in divisions], dtype=int), len(labels)
    data = MatchData(
        home=np.array([index[t] for t in home_ids], dtype=int),
        away=np.array([index[t] for t in away_ids], dtype=int),
        home_goals=np.asarray(home_goals, dtype=float),
        away_goals=np.asarray(away_goals, dtype=float),
        weights=np.ones(n) if weights is None else np.asarray(weights, dtype=float),
        n_teams=len(teams),
        division=div,
        n_divisions=n_div,
    )
    return data, teams


def _sum_to_zero(free: np.ndarray) -> np.ndarray:
    return np.append(free, -free.sum())


def _unpack(x: np.ndarray, n: int, n_div: int):
    attack = _sum_to_zero(x[: n - 1])
    defence = _sum_to_zero(x[n - 1 : 2 * n - 2])
    bases = x[2 * n - 2 : 2 * n - 2 + n_div]
    home, rho = x[2 * n - 2 + n_div], x[2 * n - 1 + n_div]
    return attack, defence, bases, home, rho


def _objective(x: np.ndarray, data: MatchData, penalty: float,
               prior_attack: np.ndarray | None = None, prior_defence: np.ndarray | None = None) -> tuple[float, np.ndarray]:
    """Weighted negative log-likelihood (plus ridge penalty towards the priors) and its exact gradient."""
    n, n_div = data.n_teams, data.n_divisions
    attack, defence, bases, home, rho = _unpack(x, n, n_div)
    h, a, hg, ag, w = data.home, data.away, data.home_goals, data.away_goals, data.weights
    div = data.division if data.division is not None else np.zeros(len(h), dtype=int)
    pa = np.zeros(n) if prior_attack is None else prior_attack
    pd_ = np.zeros(n) if prior_defence is None else prior_defence

    eta_h = bases[div] + home + attack[h] + defence[a]
    eta_a = bases[div] + attack[a] + defence[h]
    lam, mu = np.exp(eta_h), np.exp(eta_a)

    # Dixon-Coles correction and the derivatives of log(tau).
    tau = np.ones_like(lam)
    dt_h, dt_a, dt_rho = np.zeros_like(lam), np.zeros_like(lam), np.zeros_like(lam)
    m00 = (hg == 0) & (ag == 0)
    m01 = (hg == 0) & (ag == 1)
    m10 = (hg == 1) & (ag == 0)
    m11 = (hg == 1) & (ag == 1)
    tau[m00] = 1 - lam[m00] * mu[m00] * rho
    tau[m01] = 1 + lam[m01] * rho
    tau[m10] = 1 + mu[m10] * rho
    tau[m11] = 1 - rho
    tau = np.maximum(tau, 1e-10)
    dt_h[m00] = dt_a[m00] = -lam[m00] * mu[m00] * rho / tau[m00]
    dt_rho[m00] = -lam[m00] * mu[m00] / tau[m00]
    dt_h[m01] = lam[m01] * rho / tau[m01]
    dt_rho[m01] = lam[m01] / tau[m01]
    dt_a[m10] = mu[m10] * rho / tau[m10]
    dt_rho[m10] = mu[m10] / tau[m10]
    dt_rho[m11] = -1 / tau[m11]

    # Log-likelihood without the constant log(goals!) terms.
    ll = w * (np.log(tau) + hg * eta_h - lam + ag * eta_a - mu)
    da, dd = attack - pa, defence - pd_
    nll = -ll.sum() + penalty * (da @ da + dd @ dd)

    g_h = w * (hg - lam + dt_h)          # d ll / d eta_h, per match
    g_a = w * (ag - mu + dt_a)           # d ll / d eta_a, per match
    g_attack = np.bincount(h, g_h, n) + np.bincount(a, g_a, n)
    g_defence = np.bincount(a, g_h, n) + np.bincount(h, g_a, n)

    # Back to the negative log-likelihood, add the penalty, then map through the sum-to-zero constraint.
    g_attack = -g_attack + 2 * penalty * da
    g_defence = -g_defence + 2 * penalty * dd
    grad = np.concatenate([
        g_attack[:-1] - g_attack[-1],
        g_defence[:-1] - g_defence[-1],
        -np.bincount(div, g_h + g_a, n_div),
        [-g_h.sum(), -(w * dt_rho).sum()],
    ])
    return nll, grad


@dataclass(frozen=True)
class DixonColes:
    teams: list
    attack: np.ndarray
    defence: np.ndarray
    bases: np.ndarray
    home_adv: float
    rho: float
    converged: bool

    @property
    def base(self) -> float:
        """Goal level of the first (or only) division."""
        return float(self.bases[0])

    def index(self, team) -> int:
        return self.teams.index(team)

    def rates(self, home_team, away_team, division: int = 0) -> tuple[float, float]:
        """Expected goals (home, away) for two teams the model knows, in a match of the given division."""
        i, j = self.index(home_team), self.index(away_team)
        base = self.bases[division]
        lam = np.exp(base + self.home_adv + self.attack[i] + self.defence[j])
        mu = np.exp(base + self.attack[j] + self.defence[i])
        return float(lam), float(mu)

    def score_matrix(self, home_team, away_team, division: int = 0, max_goals: int = MAX_GOALS) -> np.ndarray:
        lam, mu = self.rates(home_team, away_team, division)
        return score_matrix(lam, mu, self.rho, max_goals)


def fit(data: MatchData, teams: list, penalty: float = 0.0, prior_attack: np.ndarray | None = None,
        prior_defence: np.ndarray | None = None, fix_rho: bool = False) -> DixonColes:
    """Maximum-likelihood fit (L-BFGS-B with the exact gradient).

    prior_attack / prior_defence: per-team ratings the penalty pulls towards (default 0, the average).
    fix_rho: keep rho at 0 (plain Poisson), e.g. for shots on target.
    """
    n, n_div = data.n_teams, data.n_divisions
    if n < 2:
        raise ValueError("need at least two teams")
    div = data.division if data.division is not None else np.zeros(len(data.home), dtype=int)
    x0 = np.zeros(2 * n + n_div)
    for k in range(n_div):          # each division's goals per team per match
        sel = div == k
        total = data.home_goals[sel].sum() + data.away_goals[sel].sum()
        x0[2 * n - 2 + k] = np.log(max(total, 1.0) / max(2 * sel.sum(), 1))
    x0[2 * n - 2 + n_div] = 0.25    # typical home advantage
    bounds = ([RATING_BOUNDS] * (2 * n - 2) + [BASE_BOUNDS] * n_div
              + [HOME_BOUNDS, (0.0, 0.0) if fix_rho else RHO_BOUNDS])
    res = minimize(_objective, x0, args=(data, penalty, prior_attack, prior_defence), jac=True,
                   method="L-BFGS-B", bounds=bounds)
    attack, defence, bases, home, rho = _unpack(res.x, n, n_div)
    return DixonColes(list(teams), attack, defence, np.array(bases), float(home), float(rho), bool(res.success))


def score_matrix(lam: float, mu: float, rho: float = 0.0, max_goals: int = MAX_GOALS) -> np.ndarray:
    """P(home scores i, away scores j) for i, j in 0..max_goals, with the low-score correction, summing to 1."""
    goals = np.arange(max_goals + 1)
    m = np.outer(poisson.pmf(goals, lam), poisson.pmf(goals, mu))
    m[0, 0] *= 1 - lam * mu * rho
    m[0, 1] *= 1 + lam * rho
    m[1, 0] *= 1 + mu * rho
    m[1, 1] *= 1 - rho
    m = np.clip(m, 0.0, None)
    return m / m.sum()
