"""Market-implied goals rates: the expected goals that make a score grid reproduce market prices.

Given de-margined 1X2 and over 2.5 probabilities, solve for (lambda, mu) so the Dixon-Coles grid matches
them; every other market (builder combos, correct score, half-time...) can then be derived consistently.
"""

import numpy as np
from scipy.optimize import least_squares

from engrebodds_model.dixon_coles import score_matrix
from engrebodds_model.markets import prob


def implied_rates(p_home: float, p_away: float, p_over25: float, rho: float = -0.05) -> tuple[float, float]:
    """(lambda, mu) whose grid best matches P(home win), P(away win) and P(over 2.5)."""
    target = np.array([p_home, p_away, p_over25])

    def resid(v):
        m = score_matrix(np.exp(v[0]), np.exp(v[1]), rho)
        return np.array([prob(m, "1X2", "1"), prob(m, "1X2", "2"), prob(m, "OU", "O", 2.5)]) - target

    sol = least_squares(resid, np.log([1.4, 1.1]), bounds=(np.log(0.05), np.log(6.0)))
    return float(np.exp(sol.x[0])), float(np.exp(sol.x[1]))
