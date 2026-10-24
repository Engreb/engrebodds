"""Calibration of market probabilities: logistic maps for two-way markets and a draw shift for 1X2.

A map turns a model probability p into sigmoid(a + b * logit(p)); a = 0, b = 1 is the identity.
Maps are keyed by the "positive" selection (e.g. "OU|O|2.5", "BTTS|Y"); the other side becomes 1 - p'.
Fit them only on predictions from seasons *before* the ones they are applied to.
"""

import json
from dataclasses import asdict, dataclass, field

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit

EPS = 1e-6

# Positive selection -> its complement, for every two-way market family we calibrate.
PAIRS = {"OU|O": "OU|U", "BTTS|Y": "BTTS|N", "TEAM_OU|HO": "TEAM_OU|HU", "TEAM_OU|AO": "TEAM_OU|AU",
         "CLEAN_SHEET|H": None, "CLEAN_SHEET|A": None}


@dataclass(frozen=True)
class LogisticMap:
    a: float = 0.0
    b: float = 1.0

    def apply(self, p):
        p = np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)
        return expit(self.a + self.b * logit(p))


def fit_map(p, hit) -> LogisticMap:
    """Maximum-likelihood logistic map (b kept positive so the map stays increasing)."""
    z = logit(np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS))
    y = np.asarray(hit, dtype=float)

    def nll(v):
        q = np.clip(expit(v[0] + v[1] * z), EPS, 1 - EPS)
        return -np.mean(y * np.log(q) + (1 - y) * np.log(1 - q))

    res = minimize(nll, [0.0, 1.0], method="L-BFGS-B", bounds=[(-3, 3), (0.05, 5)])
    return LogisticMap(float(res.x[0]), float(res.x[1]))


def shift_draw(p3, d: float) -> np.ndarray:
    """Add d to the draw's log-odds and renormalise; rows are (home, draw, away)."""
    p3 = np.clip(np.asarray(p3, dtype=float), EPS, 1.0)
    w = p3.copy()
    w[..., 1] *= np.exp(d)
    return w / w.sum(axis=-1, keepdims=True)


def fit_draw_shift(p3, outcome) -> float:
    """The draw shift minimising 1X2 log loss (outcome: 0 home, 1 draw, 2 away)."""
    p3, y = np.asarray(p3, dtype=float), np.asarray(outcome)
    rows = np.arange(len(y))

    def nll(v):
        return -np.mean(np.log(shift_draw(p3, v[0])[rows, y]))

    return float(minimize(nll, [0.0], method="L-BFGS-B", bounds=[(-1, 1)]).x[0])


@dataclass
class Calibrator:
    """Maps per positive selection key (e.g. "OU|O|2.5") plus a 1X2 draw shift."""
    maps: dict[str, LogisticMap] = field(default_factory=dict)
    draw_shift: float = 0.0

    def apply(self, probs: dict[str, float]) -> dict[str, float]:
        """Calibrated copy of a probability dict keyed "MARKET|selection[|line]"."""
        out = dict(probs)
        for key, m in self.maps.items():
            if key not in probs:
                continue
            p = float(m.apply(probs[key]))
            out[key] = p
            other = _complement(key)
            if other and other in out:
                out[other] = 1 - p
        if self.draw_shift and all(k in out for k in ("1X2|1", "1X2|X", "1X2|2")):
            h, d, a = shift_draw([out["1X2|1"], out["1X2|X"], out["1X2|2"]], self.draw_shift)
            out["1X2|1"], out["1X2|X"], out["1X2|2"] = float(h), float(d), float(a)
        return out

    def to_json(self) -> str:
        return json.dumps({"draw_shift": self.draw_shift, "maps": {k: asdict(m) for k, m in self.maps.items()}})

    @classmethod
    def from_json(cls, text: str) -> "Calibrator":
        raw = json.loads(text)
        return cls({k: LogisticMap(**v) for k, v in raw["maps"].items()}, raw.get("draw_shift", 0.0))


def _complement(key: str) -> str | None:
    market, selection, *line = key.split("|")
    other = PAIRS.get(f"{market}|{selection}")
    return None if other is None else "|".join([other] + line)
