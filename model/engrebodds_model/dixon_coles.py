"""Dixon-Coles (1997) goals model with time decay and a ridge penalty.

Expected goals for home team h against away team a:
    lambda (home) = exp(base + home_adv + attack[h] + defence[a])
    mu     (away) = exp(base + attack[a] + defence[h])
`defence` is a weakness rating (higher concedes more). Attack and defence each sum to zero, so `base`
is the league's goal level and a rating of 0 means league average. The ridge penalty pulls ratings
towards 0, which keeps teams with few matches (or new to the league) close to average.
Low scores (0-0, 1-0, 0-1, 1-1) get the Dixon-Coles correction tau, controlled by rho.
Each match is weighted by exp(-xi * days_ago).
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
    """Matches as arrays, with teams as indices 0..n_teams-1."""
    home: np.ndarray
    away: np.ndarray
    home_goals: np.ndarray
    away_goals: np.ndarray
    weights: np.ndarray
    n_teams: int


def prepare(home_ids: Sequence, away_ids: Sequence, home_goals: Sequence, away_goals: Sequence,
            weights: Sequence | None = None) -> tuple[MatchData, list]:
    """Index teams and pack matches. Returns the data and the team ids in index order."""
    teams = sorted(set(home_ids) | set(away_ids))
    index = {t: i for i, t in enumerate(teams)}
    n = len(home_ids)
    data = MatchData(
        home=np.array([index[t] for t in home_ids], dtype=int),
        away=np.array([index[t] for t in away_ids], dtype=int),
        home_goals=np.asarray(home_goals, dtype=float),
        away_goals=np.asarray(away_goals, dtype=float),
        weights=np.ones(n) if weights is None else np.asarray(weights, dtype=float),
        n_teams=len(teams),
    )
    return data, teams


def _sum_to_zero(free: np.ndarray) -> np.ndarray:
    return np.append(free, -free.sum())


def _unpack(x: np.ndarray, n: int):
    attack = _sum_to_zero(x[: n - 1])
    defence = _sum_to_zero(x[n - 1 : 2 * n - 2])
    base, home, rho = x[2 * n - 2], x[2 * n - 1], x[2 * n]
    return attack, defence, base, home, rho


def _objective(x: np.ndarray, data: MatchData, penalty: float) -> tuple[float, np.ndarray]:
    """Weighted negative log-likelihood (plus ridge penalty) and its exact gradient."""
    n = data.n_teams
    attack, defence, base, home, rho = _unpack(x, n)
    h, a, hg, ag, w = data.home, data.away, data.home_goals, data.away_goals, data.weights

    eta_h = base + home + attack[h] + defence[a]
    eta_a = base + attack[a] + defence[h]
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
    nll = -ll.sum() + penalty * (attack @ attack + defence @ defence)

    g_h = w * (hg - lam + dt_h)          # d ll / d eta_h, per match
    g_a = w * (ag - mu + dt_a)           # d ll / d eta_a, per match
    g_attack = np.bincount(h, g_h, n) + np.bincount(a, g_a, n)
    g_defence = np.bincount(a, g_h, n) + np.bincount(h, g_a, n)

    # Back to the negative log-likelihood, add the penalty, then map through the sum-to-zero constraint.
    g_attack = -g_attack + 2 * penalty * attack
    g_defence = -g_defence + 2 * penalty * defence
    grad = np.concatenate([
        g_attack[:-1] - g_attack[-1],
        g_defence[:-1] - g_defence[-1],
        [-(g_h.sum() + g_a.sum()), -g_h.sum(), -(w * dt_rho).sum()],
    ])
    return nll, grad


@dataclass(frozen=True)
class DixonColes:
    teams: list
    attack: np.ndarray
    defence: np.ndarray
    base: float
    home_adv: float
    rho: float
    converged: bool

    def index(self, team) -> int:
        return self.teams.index(team)

    def rates(self, home_team, away_team) -> tuple[float, float]:
        """Expected goals (home, away) for two teams the model knows."""
        i, j = self.index(home_team), self.index(away_team)
        lam = np.exp(self.base + self.home_adv + self.attack[i] + self.defence[j])
        mu = np.exp(self.base + self.attack[j] + self.defence[i])
        return float(lam), float(mu)

    def score_matrix(self, home_team, away_team, max_goals: int = MAX_GOALS) -> np.ndarray:
        lam, mu = self.rates(home_team, away_team)
        return score_matrix(lam, mu, self.rho, max_goals)


def fit(data: MatchData, teams: list, penalty: float = 0.0) -> DixonColes:
    """Maximum-likelihood fit (L-BFGS-B with the exact gradient)."""
    n = data.n_teams
    if n < 2:
        raise ValueError("need at least two teams")
    x0 = np.zeros(2 * n + 1)
    total = data.home_goals.sum() + data.away_goals.sum()
    x0[2 * n - 2] = np.log(max(total, 1.0) / (2 * len(data.home_goals)))   # base: league goals per team per match
    x0[2 * n - 1] = 0.25                                                    # typical home advantage
    bounds = [RATING_BOUNDS] * (2 * n - 2) + [BASE_BOUNDS, HOME_BOUNDS, RHO_BOUNDS]
    res = minimize(_objective, x0, args=(data, penalty), jac=True, method="L-BFGS-B", bounds=bounds)
    attack, defence, base, home, rho = _unpack(res.x, n)
    return DixonColes(list(teams), attack, defence, float(base), float(home), float(rho), bool(res.success))


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
