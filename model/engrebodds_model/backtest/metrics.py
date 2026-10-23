"""Scoring rules, bookmaker margin removal, calibration and bootstrap intervals."""

import numpy as np

EPS = 1e-12


def demargin(*odds: np.ndarray) -> np.ndarray:
    """Implied probabilities with the margin removed proportionally. Rows with a missing price come back NaN.

    demargin(home, draw, away) -> array (n, 3); demargin(over, under) -> (n, 2).
    """
    inv = 1.0 / np.column_stack([np.asarray(o, dtype=float) for o in odds])
    return inv / inv.sum(axis=1, keepdims=True)


def log_loss(probs: np.ndarray, outcome: np.ndarray) -> float:
    """Mean negative log probability of what happened. probs (n, k), outcome = column index (n,)."""
    p = probs[np.arange(len(outcome)), outcome]
    return float(-np.mean(np.log(np.clip(p, EPS, 1.0))))


def brier(probs: np.ndarray, outcome: np.ndarray) -> float:
    onehot = np.eye(probs.shape[1])[outcome]
    return float(np.mean(np.sum((probs - onehot) ** 2, axis=1)))


def rps(probs: np.ndarray, outcome: np.ndarray) -> float:
    """Ranked probability score for ordered outcomes (home, draw, away)."""
    onehot = np.eye(probs.shape[1])[outcome]
    cum = np.cumsum(probs, axis=1) - np.cumsum(onehot, axis=1)
    return float(np.mean(np.sum(cum[:, :-1] ** 2, axis=1) / (probs.shape[1] - 1)))


def calibration(pred: np.ndarray, hit: np.ndarray, bins: int = 10) -> tuple[float, list[tuple[float, float, int]]]:
    """Expected calibration error and per-bin (mean predicted, observed frequency, count), using equal-count bins.

    pred and hit are flat arrays: every probability the model gave and whether that outcome happened.
    """
    pred, hit = np.asarray(pred, dtype=float), np.asarray(hit, dtype=float)
    order = np.argsort(pred)
    table, gaps = [], 0.0
    for chunk in np.array_split(order, bins):
        if len(chunk) == 0:
            continue
        mp, freq = pred[chunk].mean(), hit[chunk].mean()
        table.append((float(mp), float(freq), int(len(chunk))))
        gaps += len(chunk) * abs(mp - freq)
    return gaps / len(pred), table


def bootstrap_ci(values: np.ndarray, n: int = 2000, alpha: float = 0.05, seed: int = 0) -> tuple[float, float]:
    """Percentile bootstrap interval for the mean."""
    values = np.asarray(values, dtype=float)
    if len(values) == 0:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    means = values[rng.integers(0, len(values), (n, len(values)))].mean(axis=1)
    return float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2))


def max_drawdown(profits: np.ndarray) -> float:
    """Largest fall in cumulative units from a previous peak (a positive number)."""
    if len(profits) == 0:
        return 0.0
    cum = np.cumsum(profits)
    return float(np.max(np.maximum.accumulate(np.maximum(cum, 0.0)) - cum))
