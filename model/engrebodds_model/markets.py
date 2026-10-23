"""Market probabilities from score matrices, using the Phase 1 market catalogue's codes and selections.

Every selection is a mask over the score grid (rows = home goals, columns = away goals). A single
selection's probability is the sum of its cells; a combo's ("1X & O1.5") is the sum where all its
masks hold, which is exact rather than multiplying the legs.

Probabilities are keyed like predictions.probs: "MARKET|selection" or "MARKET|selection|line",
e.g. "1X2|1", "OU|O|2.5".
"""

from collections.abc import Iterable

import numpy as np
from scipy.stats import poisson

from engrebodds_model.dixon_coles import MAX_GOALS

MARGIN = 0.06                         # fair odds = (1 - MARGIN) / probability (agreed: 6% flat)
OU_LINES = (0.5, 1.5, 2.5, 3.5, 4.5)
TEAM_LINES = (0.5, 1.5, 2.5)
HT_OU_LINES = (0.5, 1.5, 2.5)
TOP_SCORES = 5

# Half-time markets settle on the half-time matrix with the same rules as their full-time counterparts.
HT_BASE = {"HT_1X2": "1X2", "HT_OU": "OU", "HT_CS": "CS"}

Leg = tuple[str, str, float | None]   # (market code, selection, line)


def key(market: str, selection: str, line: float | None = None) -> str:
    return f"{market}|{selection}" if line is None else f"{market}|{selection}|{line:g}"


def selection_mask(market: str, selection: str, line: float | None, size: int) -> np.ndarray:
    """Boolean grid of the scores where the selection wins. A total exactly on a whole line (push) is in neither side."""
    market = HT_BASE.get(market, market)
    i, j = np.indices((size, size))
    if market == "1X2":
        masks = {"1": i > j, "X": i == j, "2": i < j}
    elif market == "DC":
        masks = {"1X": i >= j, "X2": i <= j, "12": i != j}
    elif market == "OU":
        masks = {"O": i + j > line, "U": i + j < line}
    elif market == "TEAM_OU":
        masks = {"HO": i > line, "HU": i < line, "AO": j > line, "AU": j < line}
    elif market == "BTTS":
        both = (i > 0) & (j > 0)
        masks = {"Y": both, "N": ~both}
    elif market == "CLEAN_SHEET":
        masks = {"H": j == 0, "A": i == 0}
    elif market == "CS":
        h, a = (int(x) for x in selection.split("-"))
        return (i == h) & (j == a)
    else:
        raise ValueError(f"no goals rule for market {market}")
    if selection not in masks:
        raise ValueError(f"selection {selection} is not valid for {market}")
    return masks[selection]


def prob(matrix: np.ndarray, market: str, selection: str, line: float | None = None) -> float:
    return float(matrix[selection_mask(market, selection, line, matrix.shape[0])].sum())


def combo_prob(matrix: np.ndarray, legs: Iterable[Leg]) -> float:
    """Chance that every leg wins, from the joint matrix (full-time legs)."""
    mask = np.ones(matrix.shape, dtype=bool)
    for market, selection, line in legs:
        mask &= selection_mask(market, selection, line, matrix.shape[0])
    return float(matrix[mask].sum())


def ht_matrix(lam: float, mu: float, ht_share: float, max_goals: int = MAX_GOALS) -> np.ndarray:
    """Half-time score matrix: independent Poisson with each side's rate scaled by the first-half goal share."""
    goals = np.arange(max_goals + 1)
    m = np.outer(poisson.pmf(goals, lam * ht_share), poisson.pmf(goals, mu * ht_share))
    return m / m.sum()


def top_scores(matrix: np.ndarray, n: int = TOP_SCORES) -> list[tuple[str, float]]:
    flat = np.argsort(matrix, axis=None)[::-1][:n]
    return [(f"{i}-{j}", float(matrix[i, j])) for i, j in zip(*np.unravel_index(flat, matrix.shape))]


def all_markets(ft: np.ndarray, ht: np.ndarray | None = None) -> dict[str, float]:
    """Every selection the site shows, from the full-time (and, if given, half-time) matrix."""
    p: dict[str, float] = {}
    for sel in ("1", "X", "2"):
        p[key("1X2", sel)] = prob(ft, "1X2", sel)
    for sel in ("1X", "X2", "12"):
        p[key("DC", sel)] = prob(ft, "DC", sel)
    for line in OU_LINES:
        for sel in ("O", "U"):
            p[key("OU", sel, line)] = prob(ft, "OU", sel, line)
    for line in TEAM_LINES:
        for sel in ("HO", "HU", "AO", "AU"):
            p[key("TEAM_OU", sel, line)] = prob(ft, "TEAM_OU", sel, line)
    for sel in ("Y", "N"):
        p[key("BTTS", sel)] = prob(ft, "BTTS", sel)
    for sel in ("H", "A"):
        p[key("CLEAN_SHEET", sel)] = prob(ft, "CLEAN_SHEET", sel)
    for score, sp in top_scores(ft):
        p[key("CS", score)] = sp
    if ht is not None:
        for sel in ("1", "X", "2"):
            p[key("HT_1X2", sel)] = prob(ht, "HT_1X2", sel)
        for line in HT_OU_LINES:
            for sel in ("O", "U"):
                p[key("HT_OU", sel, line)] = prob(ht, "HT_OU", sel, line)
        for score, sp in top_scores(ht):
            p[key("HT_CS", score)] = sp
    return p


def fair_odds(p: float, margin: float = MARGIN) -> float:
    """Model price including a typical bookmaker margin: (1 - margin) / p, never below 1.01."""
    if p <= 0:
        raise ValueError("probability must be positive")
    return max(1.01, round((1 - margin) / p, 2))
