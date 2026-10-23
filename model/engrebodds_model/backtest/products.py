"""Simulate both tip products and the slips on walk-forward predictions, and summarise them.

Rules follow docs/phase-2-plan.md:
- Most-likely: result (most likely 1/X/2), goals (most likely over/under priced >= 1.30), GG/NG, builder
  (most likely result-or-double-chance x over/under combo priced >= 1.50; never has a market price).
- Value: 1X2 and O/U 2.5 only, at most one per match (highest edge), where p x price >= 1 + edge.
- Tips without a market price count in hit rate only, never in units or ROI.
- Reference price (backtest stand-in for 1xBet): 1xBet early, else Betway early (both operate in Nigeria),
  else Bet365 early. O/U 2.5 has Bet365 only in these files.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from engrebodds_model.backtest.metrics import bootstrap_ci, demargin, max_drawdown
from engrebodds_model.dixon_coles import score_matrix
from engrebodds_model.markets import OU_LINES, combo_prob, fair_odds, prob, selection_mask

RESULT_COLS = {"1": "h", "X": "d", "2": "a"}
OU25_COLS = {"O": "o25", "U": "u25"}
REF_1X2 = (("xb", "1xBet"), ("bw", "Betway"), ("b365", "Bet365"))
BUILDER_FIRST = ("1", "X", "2", "1X", "X2", "12")
SLIP_TARGETS = {"odds2": (2.0, 0.60), "odds5": (5.0, 0.60), "odds10": (10.0, 0.60), "low_risk": (1.6, 0.80)}
SLIP_MIN_LEG_PRICE = 1.12


@dataclass(frozen=True)
class ProductConfig:
    goals_min: float = 1.30
    builder_min: float = 1.50
    edge: float = 0.03
    min_quality: float = 0.6


def _finite(x) -> bool:
    return x is not None and np.isfinite(x)


def market_price(row, prefix: str, market: str, selection: str, line) -> float:
    if market == "1X2":
        return row.get(f"{prefix}_{RESULT_COLS[selection]}", np.nan)
    if market == "OU" and line == 2.5:
        return row.get(f"{prefix}_{OU25_COLS[selection]}", np.nan)
    return np.nan


def ref_price(row, market: str, selection: str, line) -> tuple[float, str | None]:
    if market == "1X2":
        for prefix, name in REF_1X2:
            price = market_price(row, prefix, market, selection, line)
            if _finite(price):
                return float(price), name
    elif market == "OU" and line == 2.5:
        price = market_price(row, "b365", market, selection, line)
        if _finite(price):
            return float(price), "Bet365"
    return np.nan, None


def close_fair_prob(row, market: str, selection: str, line, benchmark: str) -> float:
    """Closing probability with the margin removed, from the benchmark ('psc' = Pinnacle, 'avgc' = market average)."""
    if market == "1X2":
        p = demargin([row.get(f"{benchmark}_h")], [row.get(f"{benchmark}_d")], [row.get(f"{benchmark}_a")])[0]
        return float(p[{"1": 0, "X": 1, "2": 2}[selection]])
    if market == "OU" and line == 2.5:
        p = demargin([row.get(f"{benchmark}_o25")], [row.get(f"{benchmark}_u25")])[0]
        return float(p[0 if selection == "O" else 1])
    return np.nan


def won(legs, hg: int, ag: int) -> bool:
    size = max(11, hg + 1, ag + 1)
    return all(bool(selection_mask(m, s, l, size)[hg, ag]) for m, s, l in legs)


def _describe(legs) -> str:
    return " & ".join(f"{m} {s}" + ("" if l is None else f" {l:g}") for m, s, l in legs)


def match_tips(row, cfg: ProductConfig, benchmark: str) -> list[dict]:
    """Both products' tips for one predicted match (empty when data quality is too low)."""
    if row["dq"] < cfg.min_quality:
        return []
    ft = score_matrix(row["lam"], row["mu"], row["rho"])
    hg, ag = int(row["hg"]), int(row["ag"])
    base = {k: row[k] for k in ("league", "season", "date", "home", "away")}
    tips = []

    def add(product, slot, legs, p, price=np.nan, source=None):
        single = len(legs) == 1
        m, s, l = legs[0]
        tip = {**base, "product": product, "slot": slot, "tip": _describe(legs), "p": p, "fair": fair_odds(p),
               "price": price, "source": source, "won": won(legs, hg, ag),
               "price_b365": market_price(row, "b365", m, s, l) if single else np.nan,
               "price_b365c": market_price(row, "b365c", m, s, l) if single else np.nan,
               "clv": np.nan, "single": single, "market": m, "selection": s, "line": l}
        if _finite(price) and single:
            pc = close_fair_prob(row, m, s, l, benchmark)
            if _finite(pc):
                tip["clv"] = price * pc - 1
        tips.append(tip)

    # Most-likely: result
    p1x2 = {s: prob(ft, "1X2", s) for s in RESULT_COLS}
    s = max(p1x2, key=p1x2.get)
    add("likely", "result", [("1X2", s, None)], p1x2[s], *ref_price(row, "1X2", s, None))

    # Most-likely: goals (most likely over/under whose price, market or fair, is at least goals_min)
    best = None
    for line in OU_LINES:
        for side in ("O", "U"):
            p = prob(ft, "OU", side, line)
            price, source = ref_price(row, "OU", side, line)
            if (price if _finite(price) else fair_odds(p)) >= cfg.goals_min and (best is None or p > best[0]):
                best = (p, side, line, price, source)
    if best:
        p, side, line, price, source = best
        add("likely", "goals", [("OU", side, line)], p, price, source)

    # Most-likely: GG/NG
    p_y = prob(ft, "BTTS", "Y")
    add("likely", "btts", [("BTTS", "Y" if p_y >= 0.5 else "N", None)], max(p_y, 1 - p_y))

    # Most-likely: builder (no market price exists for combos)
    best = None
    for first in BUILDER_FIRST:
        market = "1X2" if first in RESULT_COLS else "DC"
        for line in OU_LINES:
            for side in ("O", "U"):
                legs = [(market, first, None), ("OU", side, line)]
                p = combo_prob(ft, legs)
                if p > 0 and fair_odds(p) >= cfg.builder_min and (best is None or p > best[0]):
                    best = (p, legs)
    if best:
        add("likely", "builder", best[1], best[0])

    # Value: 1X2 and O/U 2.5 only, the single best edge
    cands = [("1X2", s, None, p1x2[s]) for s in RESULT_COLS]
    cands += [("OU", side, 2.5, prob(ft, "OU", side, 2.5)) for side in ("O", "U")]
    best = None
    for m, s, l, p in cands:
        price, source = ref_price(row, m, s, l)
        if _finite(price) and p * price >= 1 + cfg.edge and (best is None or p * price > best[0]):
            best = (p * price, m, s, l, p, price, source)
    if best:
        _, m, s, l, p, price, source = best
        add("value", m.lower(), [(m, s, l)], p, price, source)
    return tips


def simulate(pred: pd.DataFrame, cfg: ProductConfig, benchmark: str = "psc") -> pd.DataFrame:
    rows = [t for _, r in pred.iterrows() for t in match_tips(r, cfg, benchmark)]
    return pd.DataFrame(rows)


def slips(tips: pd.DataFrame) -> pd.DataFrame:
    """Daily 2/5/10-odds and lowest-risk slips from priced single-leg most-likely tips, one leg per match."""
    pool = tips[(tips["product"] == "likely") & tips["single"] & tips["price"].notna()
                & (tips["price"] >= SLIP_MIN_LEG_PRICE)].copy()
    pool["day"] = pool["date"].dt.date
    out = []
    for day, group in pool.groupby("day"):
        group = group.sort_values("p", ascending=False)
        for kind, (target, min_p) in SLIP_TARGETS.items():
            used, legs, price, p_all = set(), [], 1.0, 1.0
            for _, t in group.iterrows():
                if price >= target:
                    break
                match = (t["home"], t["away"])
                if t["p"] < min_p or match in used:
                    continue
                used.add(match)
                legs.append(t)
                price *= t["price"]
                p_all *= t["p"]
            if legs:
                out.append({"date": pd.Timestamp(day), "season": legs[0]["season"], "kind": kind, "legs": len(legs),
                            "price": price, "p_all": p_all, "reached": price >= target,
                            "won": all(bool(l["won"]) for l in legs)})
    return pd.DataFrame(out)


def summarise(tips: pd.DataFrame, price_col: str = "price") -> dict:
    """Hit rate (every tip), and units/ROI/average odds (priced tips only), with bootstrap intervals."""
    n = len(tips)
    if n == 0:
        return {"tips": 0}
    priced = tips[tips[price_col].notna()]
    profit = np.where(priced["won"], priced[price_col] - 1, -1.0)
    roi_ci = bootstrap_ci(profit)
    clv = tips["clv"].dropna().to_numpy() if "clv" in tips else np.array([])
    clv_ci = bootstrap_ci(clv)
    return {
        "tips": n, "hit_rate": float(tips["won"].mean()),
        "priced": len(priced), "no_market_price": n - len(priced),
        "avg_odds": float(priced[price_col].mean()) if len(priced) else np.nan,
        "units": float(profit.sum()), "roi": float(profit.mean()) if len(priced) else np.nan,
        "roi_lo": roi_ci[0], "roi_hi": roi_ci[1],
        "roi_inconclusive": bool(len(priced) and roi_ci[0] <= 0 <= roi_ci[1]),
        "max_drawdown": max_drawdown(profit),
        "clv_n": len(clv), "clv": float(clv.mean()) if len(clv) else np.nan, "clv_lo": clv_ci[0], "clv_hi": clv_ci[1],
    }
