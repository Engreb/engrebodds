import numpy as np
import pandas as pd
import pytest

from engrebodds_model.backtest.metrics import (
    bootstrap_ci, brier, calibration, demargin, log_loss, max_drawdown, rps,
)
from engrebodds_model.backtest.products import ProductConfig, match_tips, slips, summarise, won
from engrebodds_model.backtest.walkforward import Config, walk_forward


# ---------- metrics ----------

def test_demargin_removes_the_margin():
    p = demargin([2.0], [3.4], [4.2])[0]
    assert p.sum() == pytest.approx(1.0)
    assert p[0] == pytest.approx((1 / 2.0) / (1 / 2.0 + 1 / 3.4 + 1 / 4.2))
    assert np.isnan(demargin([2.0], [np.nan])[0]).all()


def test_scoring_rules_on_known_values():
    probs = np.array([[0.5, 0.3, 0.2], [0.2, 0.3, 0.5]])
    outcome = np.array([0, 1])
    assert log_loss(probs, outcome) == pytest.approx(-(np.log(0.5) + np.log(0.3)) / 2)
    assert brier(probs, outcome) == pytest.approx(((0.25 + 0.09 + 0.04) + (0.04 + 0.49 + 0.25)) / 2)
    # RPS: perfect forecast scores 0, the worst ordered miss scores 1.
    assert rps(np.array([[1.0, 0, 0]]), np.array([0])) == 0.0
    assert rps(np.array([[0, 0, 1.0]]), np.array([0])) == pytest.approx(1.0)


def test_calibration_of_a_calibrated_forecast_is_small():
    rng = np.random.default_rng(3)
    pred = rng.uniform(0.05, 0.95, 20_000)
    hit = rng.uniform(size=pred.size) < pred
    ece, table = calibration(pred, hit)
    assert ece < 0.015 and len(table) == 10
    biased, _ = calibration(np.clip(pred + 0.1, 0, 1), hit)
    assert biased > 0.08


def test_bootstrap_and_drawdown():
    lo, hi = bootstrap_ci(np.r_[np.ones(500), -np.ones(500)])
    assert lo < 0 < hi
    assert max_drawdown(np.array([1, 1, -1, -1, -1, 2])) == 3.0
    assert max_drawdown(np.array([-1, -1])) == 2.0


# ---------- walk-forward: no look-ahead ----------

def league(seed=0, weeks=150):
    rng = np.random.default_rng(seed)
    teams = [f"T{i}" for i in range(10)]
    rows, day = [], pd.Timestamp("2020-08-01")
    for w in range(weeks):
        order = rng.permutation(teams)
        for k in range(0, 10, 2):
            rows.append({"league": "X", "season": "s", "date": day + pd.Timedelta(days=7 * w),
                         "home": order[k], "away": order[k + 1], "hg": rng.poisson(1.5), "ag": rng.poisson(1.1),
                         "hthg": 0, "htag": 0})
    return pd.DataFrame(rows)


def test_walk_forward_never_sees_the_future():
    df = league()
    split = pd.Timestamp("2022-06-01")
    poisoned = df.copy()
    poisoned.loc[poisoned.date >= split, ["hg", "ag"]] = [9, 0]     # absurd results after the split
    cfg = Config(xi=0.002, penalty=2.0)
    start = pd.Timestamp("2021-08-01")
    clean = walk_forward(df[df.date < split], start, split, cfg)
    dirty = walk_forward(poisoned, start, split, cfg)
    assert len(clean) > 100
    pd.testing.assert_series_equal(clean["p_h"], dirty["p_h"])
    assert (dirty["cutoff"] <= dirty["date"]).all()


# ---------- products ----------

def row(**overrides):
    base = {"league": "E0", "season": "2023/24", "date": pd.Timestamp("2024-01-06"), "home": "A", "away": "B",
            "hg": 2, "ag": 1, "lam": 1.8, "mu": 0.9, "rho": -0.05, "dq": 1.0,
            "xb_h": np.nan, "xb_d": np.nan, "xb_a": np.nan, "bw_h": 1.70, "bw_d": 3.9, "bw_a": 5.0,
            "b365_h": 1.72, "b365_d": 3.8, "b365_a": 4.8, "b365c_h": 1.65, "b365c_d": 4.0, "b365c_a": 5.2,
            "b365_o25": 1.80, "b365_u25": 2.05, "b365c_o25": 1.75, "b365c_u25": 2.10,
            "psc_h": 1.62, "psc_d": 4.1, "psc_a": 5.6, "psc_o25": 1.74, "psc_u25": 2.16}
    base.update(overrides)
    return pd.Series(base)


def test_most_likely_slots_and_prices():
    tips = {t["slot"]: t for t in match_tips(row(), ProductConfig(), "psc") if t["product"] == "likely"}
    assert set(tips) == {"result", "goals", "btts", "builder"}
    assert tips["result"]["tip"] == "1X2 1" and tips["result"]["source"] == "Betway"   # 1xBet missing -> Betway
    assert tips["result"]["price"] == 1.70 and tips["result"]["won"]
    assert np.isnan(tips["builder"]["price"]) and tips["builder"]["fair"] >= 1.50       # never a market price
    assert np.isnan(tips["btts"]["price"])
    goals = tips["goals"]
    assert (goals["price"] if not np.isnan(goals["price"]) else goals["fair"]) >= 1.30


def test_value_tip_rule():
    # A generous away price creates an edge on the away win only.
    tips = [t for t in match_tips(row(bw_a=9.0), ProductConfig(edge=0.03), "psc") if t["product"] == "value"]
    assert len(tips) == 1
    t = tips[0]
    assert t["tip"] == "1X2 2" and t["price"] == 9.0 and t["p"] * 9.0 >= 1.03
    assert t["clv"] == pytest.approx(9.0 * demargin([1.62], [4.1], [5.6])[0][2] - 1)
    assert not t["won"]
    none = [t for t in match_tips(row(), ProductConfig(edge=0.5), "psc") if t["product"] == "value"]
    assert none == []


def test_low_quality_matches_get_no_tips():
    assert match_tips(row(dq=0.5), ProductConfig(), "psc") == []


def test_won_handles_combos():
    assert won([("DC", "1X", None), ("OU", "O", 1.5)], 2, 1)
    assert not won([("DC", "X2", None), ("OU", "O", 1.5)], 2, 1)


def test_summary_counts_unpriced_in_hit_rate_only():
    tips = pd.DataFrame({"won": [True, False, True], "price": [2.0, 3.0, np.nan], "clv": [0.05, -0.02, np.nan]})
    s = summarise(tips)
    assert s["hit_rate"] == pytest.approx(2 / 3)
    assert s["priced"] == 2 and s["no_market_price"] == 1
    assert s["units"] == pytest.approx((2.0 - 1) - 1)
    assert s["roi"] == pytest.approx(0.0)
    assert s["clv_n"] == 2


def test_slips_one_leg_per_match_and_reach_target():
    tips = []
    for i, (p, price) in enumerate([(0.8, 1.3), (0.75, 1.4), (0.7, 1.5), (0.65, 1.6), (0.62, 1.7)]):
        tips.append({"product": "likely", "single": True, "price": price, "p": p, "won": True, "season": "s",
                     "date": pd.Timestamp("2024-01-06"), "home": f"H{i}", "away": f"A{i}"})
    tips.append({**tips[0], "p": 0.9})          # second tip on the same match must not be used
    s = slips(pd.DataFrame(tips)).set_index("kind")
    # Highest probability first, H0 used once: 1.30 x 1.40 = 1.82 is short of 2, so a third leg (1.50) is added.
    assert s.loc["odds2", "legs"] == 3 and s.loc["odds2", "price"] == pytest.approx(1.3 * 1.4 * 1.5)
    assert s.loc["low_risk", "legs"] == 1        # only one distinct match has p >= 0.80
