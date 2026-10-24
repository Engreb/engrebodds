"""Market-implied goals rates: the expected goals that make a score grid reproduce market prices.

Given de-margined 1X2 and over 2.5 probabilities, solve for (lambda, mu) so the Dixon-Coles grid matches
them; every other market (builder combos, correct score, half-time...) can then be derived consistently.
"""

import numpy as np
from scipy.optimize import brentq, least_squares

from engrebodds_model.dixon_coles import score_matrix
from engrebodds_model.markets import prob


def demargin_power(prices) -> np.ndarray:
    """Probabilities from one market's prices with the margin removed by the power method.

    Finds k so that sum((1/price)^k) = 1 and returns (1/price)^k. Unlike proportional removal it takes
    more margin off longshots than favourites, which counters the favourite-longshot bias.
    Used live (1xBet); the backtest's experiment 0 used proportional removal (backtest/metrics.demargin).
    """
    q = 1.0 / np.asarray(prices, dtype=float)
    if not np.all(np.isfinite(q)) or np.any(q <= 0):
        return np.full(q.shape, np.nan)
    if abs(q.sum() - 1.0) < 1e-12:
        return q
    k = brentq(lambda k: np.sum(q ** k) - 1.0, 1e-3, 50.0)
    return q ** k


def implied_rates(p_home: float, p_away: float, p_over25: float, rho: float = -0.05) -> tuple[float, float]:
    """(lambda, mu) whose grid best matches P(home win), P(away win) and P(over 2.5)."""
    target = np.array([p_home, p_away, p_over25])

    def resid(v):
        m = score_matrix(np.exp(v[0]), np.exp(v[1]), rho)
        return np.array([prob(m, "1X2", "1"), prob(m, "1X2", "2"), prob(m, "OU", "O", 2.5)]) - target

    sol = least_squares(resid, np.log([1.4, 1.1]), bounds=(np.log(0.05), np.log(6.0)))
    return float(np.exp(sol.x[0])), float(np.exp(sol.x[1]))
