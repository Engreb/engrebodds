import numpy as np
import pytest

from engrebodds_model.calibration import Calibrator, LogisticMap, fit_draw_shift, fit_map, shift_draw
from engrebodds_model.dixon_coles import score_matrix
from engrebodds_model.implied import implied_rates
from engrebodds_model.markets import prob


def test_map_is_near_identity_when_already_calibrated():
    rng = np.random.default_rng(0)
    p = rng.uniform(0.1, 0.9, 30_000)
    hit = rng.uniform(size=p.size) < p
    m = fit_map(p, hit)
    assert abs(m.a) < 0.05 and abs(m.b - 1) < 0.05


def test_map_fixes_overconfidence_and_stays_increasing():
    rng = np.random.default_rng(1)
    true = rng.uniform(0.2, 0.8, 30_000)
    hit = rng.uniform(size=true.size) < true
    overconfident = np.clip(0.5 + 1.6 * (true - 0.5), 0.01, 0.99)      # stretched away from 0.5
    m = fit_map(overconfident, hit)
    assert m.b < 1                                                    # pulls back towards 0.5
    fixed = m.apply(overconfident)
    assert np.mean(np.abs(fixed - true)) < np.mean(np.abs(overconfident - true)) / 2
    grid = m.apply(np.linspace(0.05, 0.95, 50))
    assert np.all(np.diff(grid) > 0)


def test_draw_shift():
    p3 = np.tile([0.45, 0.24, 0.31], (40_000, 1))
    rng = np.random.default_rng(2)
    outcome = rng.choice(3, size=len(p3), p=[0.44, 0.27, 0.29])        # more draws than predicted
    d = fit_draw_shift(p3, outcome)
    assert d > 0
    assert shift_draw(p3[:1], d)[0, 1] == pytest.approx(0.27, abs=0.01)
    assert shift_draw(p3[:1], 0.0)[0] == pytest.approx(p3[0])


def test_calibrator_applies_maps_complements_and_round_trips():
    cal = Calibrator({"OU|O|2.5": LogisticMap(-0.2, 0.8), "BTTS|Y": LogisticMap(0.0, 0.7)}, draw_shift=0.05)
    probs = {"OU|O|2.5": 0.6, "OU|U|2.5": 0.4, "BTTS|Y": 0.7, "BTTS|N": 0.3,
             "OU|O|1.5": 0.8, "1X2|1": 0.5, "1X2|X": 0.25, "1X2|2": 0.25}
    out = cal.apply(probs)
    assert out["OU|O|2.5"] + out["OU|U|2.5"] == pytest.approx(1.0)
    assert out["BTTS|Y"] < 0.7 and out["BTTS|Y"] + out["BTTS|N"] == pytest.approx(1.0)
    assert out["OU|O|1.5"] == 0.8                                    # no map for this line: unchanged
    assert out["1X2|X"] > 0.25 and sum(out[k] for k in ("1X2|1", "1X2|X", "1X2|2")) == pytest.approx(1.0)
    again = Calibrator.from_json(cal.to_json())
    assert again.apply(probs) == pytest.approx(out)


def test_implied_rates_reproduce_market_prices():
    m = score_matrix(1.7, 0.9, -0.05)
    target = (prob(m, "1X2", "1"), prob(m, "1X2", "2"), prob(m, "OU", "O", 2.5))
    lam, mu = implied_rates(*target)
    assert lam == pytest.approx(1.7, abs=0.02) and mu == pytest.approx(0.9, abs=0.02)


def test_power_demargin_sums_to_one_and_favours_the_favourite():
    from engrebodds_model.backtest.metrics import demargin
    from engrebodds_model.implied import demargin_power
    prices = [1.45, 4.5, 7.5]                     # about 6% margin
    pw = demargin_power(prices)
    prop = demargin([prices[0]], [prices[1]], [prices[2]])[0]
    assert pw.sum() == pytest.approx(1.0)
    assert pw[0] > prop[0] and pw[2] < prop[2]     # more margin taken off the longshot
    assert np.isnan(demargin_power([1.9, np.nan])).all()
    assert demargin_power([2.0, 2.0]) == pytest.approx([0.5, 0.5])
