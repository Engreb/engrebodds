"""Most-likely product: choosing tips, free fixtures, the banker and slips (pure functions, no database).

Rules (docs/phase-2-step-4-plan.md), thresholds shared with the backtest (backtest/products.py):
- result: most likely 1/X/2 by p_final; goals: most likely over/under (0.5-4.5) priced >= 1.30 (1xBet price where
  present, else fair odds), p_final for the 2.5 line, p_model otherwise; GG/NG: more likely side (p_model);
  builder: most likely result-or-double-chance x over/under combo from the model's score grid, fair >= 1.50,
  never a market price.
- p_final = the market (1xBet, power de-margined) where predict stored one, else p_model.
- Each tip carries the 1xBet price and its capture time when 1xBet priced that exact selection.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta

import numpy as np
import pandas as pd

from engrebodds_model.backtest.products import BUILDER_FIRST, RESULT_COLS, SLIP_MIN_LEG_PRICE, SLIP_TARGETS, ProductConfig
from engrebodds_model.dixon_coles import score_matrix
from engrebodds_model.markets import OU_LINES, combo_prob, fair_odds, key

WINDOW_START = time(6, 45)        # publishing day: 06:45 UTC to 06:45 UTC the next day
FREE_FIXTURES = 3
BANKER_MIN_PRICE = 1.30
BANKERS_SLIP_LEGS = 3


@dataclass(frozen=True)
class Price:
    price: float
    captured_at: datetime
    bookmaker_id: int


def publishing_window(now: datetime) -> tuple[datetime, datetime]:
    """The publishing day containing `now` (UTC): fixed by the clock, so a late run keeps the same window."""
    now = pd.Timestamp(now).to_pydatetime()
    start = datetime.combine(now.date(), WINDOW_START)
    if now < start:
        start -= timedelta(days=1)
    return start, start + timedelta(days=1)


def confidence(p: float, quality: float) -> float:
    return round(float(np.clip((p - 0.40) / 0.40 * 10, 0, 10) * quality), 1)


def select_tips(pred: dict, prices: dict, cfg: ProductConfig = ProductConfig()) -> list[dict]:
    """The 4 most-likely tips for one prediction. prices: {(market, selection, line): Price} from 1xBet."""
    probs, final = pred["probs"], pred.get("final") or {}

    def p_final(market, selection, line=None):
        k = key(market, selection, line)
        return (final[k]["p"], "market") if k in final else (probs[k], "model")

    def tip(slot, legs, p, source):
        single = len(legs) == 1
        price = prices.get(legs[0]) if single else None
        return {"slot": slot, "legs": legs, "p": float(p), "p_source": source, "fair": fair_odds(p),
                "confidence": confidence(p, float(pred["data_quality"])),
                "price": price.price if price else None, "captured_at": price.captured_at if price else None,
                "bookmaker_id": price.bookmaker_id if price else None}

    tips = []
    # Result: most likely of 1 / X / 2.
    result = {s: p_final("1X2", s) for s in RESULT_COLS}
    s = max(result, key=lambda x: result[x][0])
    tips.append(tip("result", [("1X2", s, None)], *result[s]))

    # Goals: most likely over/under whose price (1xBet, else fair) is at least goals_min.
    best = None
    for line in OU_LINES:
        for side in ("O", "U"):
            p, source = p_final("OU", side, line) if line == 2.5 else (probs[key("OU", side, line)], "model")
            quote = prices.get(("OU", side, line))
            if (quote.price if quote else fair_odds(p)) >= cfg.goals_min and (best is None or p > best[0]):
                best = (p, source, line, side)
    if best:
        p, source, line, side = best
        tips.append(tip("goals", [("OU", side, line)], p, source))

    # GG/NG: the more likely side (model).
    gg = probs[key("BTTS", "Y")]
    tips.append(tip("btts", [("BTTS", "Y" if gg >= 0.5 else "N", None)], max(gg, 1 - gg), "model"))

    # Builder: from the model's score grid; no market price exists for combos.
    grid = score_matrix(float(pred["lambda_home"]), float(pred["lambda_away"]), float(pred["rho"]))
    best = None
    for first in BUILDER_FIRST:
        market = "1X2" if first in RESULT_COLS else "DC"
        for line in OU_LINES:
            for side in ("O", "U"):
                legs = [(market, first, None), ("OU", side, line)]
                p = combo_prob(grid, legs)
                if p > 0 and fair_odds(p) >= cfg.builder_min and (best is None or p > best[0]):
                    best = (p, legs)
    if best:
        tips.append(tip("builder", best[1], best[0], "model"))
    return tips


def choose_free(candidates: list[dict], already: int = 0, n: int = FREE_FIXTURES) -> list[int]:
    """Free fixtures: league priority first (lower = higher; none = last), then earliest kick-off."""
    need = max(0, n - already)
    ranked = sorted(candidates, key=lambda c: (c["priority"] is None, c["priority"] or 0, c["kickoff"], c["fixture_id"]))
    return [c["fixture_id"] for c in ranked[:need]]


def choose_banker(tips: list[dict]) -> dict | None:
    """Highest-probability priced result or goals tip at 1.30 or more."""
    pool = [t for t in tips if t["slot"] in ("result", "goals") and t["price"] and t["price"] >= BANKER_MIN_PRICE]
    return max(pool, key=lambda t: t["p"]) if pool else None


def build_slips(tips: list[dict]) -> list[dict]:
    """The day's slips from priced single-leg tips (each with fixture_id, p, price, captured_at, locks_at).

    One leg per match, greedily by probability (as in the backtest); the combined price is the product of the
    1xBet prices; ref_captured_at is the latest leg snapshot; the slip locks at its earliest leg's lock.
    """
    pool = sorted((t for t in tips if t.get("price") and len(t["legs"]) == 1 and t["price"] >= SLIP_MIN_LEG_PRICE),
                  key=lambda t: (-t["p"], t["fixture_id"]))
    out = []
    targets = dict(SLIP_TARGETS)
    for kind, (target, min_p) in targets.items():
        used, legs, price = set(), [], 1.0
        for t in pool:
            if price >= target:
                break
            if t["p"] < min_p or t["fixture_id"] in used:
                continue
            used.add(t["fixture_id"])
            legs.append(t)
            price *= t["price"]
        if legs:
            out.append(_slip(kind, target, legs))
    bankers, used = [], set()
    for t in pool:
        if t["slot"] in ("result", "goals") and t["price"] >= BANKER_MIN_PRICE and t["fixture_id"] not in used:
            used.add(t["fixture_id"])
            bankers.append(t)
        if len(bankers) == BANKERS_SLIP_LEGS:
            break
    if bankers:
        out.append(_slip("bankers", None, bankers))
    return out


def _slip(kind: str, target, legs: list[dict]) -> dict:
    return {"kind": kind, "target": target, "legs": legs,
            "price": round(float(np.prod([t["price"] for t in legs])), 2),
            "chance": float(np.prod([t["p"] for t in legs])),
            "captured_at": max(t["captured_at"] for t in legs),
            "locks_at": min(t["locks_at"] for t in legs)}
