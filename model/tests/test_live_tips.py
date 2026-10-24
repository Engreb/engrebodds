from datetime import datetime

import pytest

from engrebodds_model.dixon_coles import score_matrix
from engrebodds_model.live.tips import (
    Price, build_slips, choose_banker, choose_free, confidence, publishing_window, select_tips,
)
from engrebodds_model.markets import all_markets


def window(s):
    return publishing_window(datetime.fromisoformat(s))


def test_publishing_window_is_fixed_by_the_clock():
    assert window("2026-10-25 06:45") == (datetime(2026, 10, 25, 6, 45), datetime(2026, 10, 26, 6, 45))
    assert window("2026-10-25 09:30")[0] == datetime(2026, 10, 25, 6, 45)      # a late "06:45" run: same window
    assert window("2026-10-25 14:45")[0] == datetime(2026, 10, 25, 6, 45)      # afternoon run: same window
    assert window("2026-10-26 03:00")[0] == datetime(2026, 10, 25, 6, 45)      # before 06:45: previous window


def prediction(final=None, quality=1.0):
    grid = score_matrix(1.7, 0.9, -0.05)
    return {"probs": all_markets(grid), "final": final or {}, "lambda_home": 1.7, "lambda_away": 0.9,
            "rho": -0.05, "data_quality": quality}


AT = datetime(2026, 10, 25, 7, 0)


def test_four_slots_with_prices_and_sources():
    prices = {("1X2", "1", None): Price(1.85, AT, 11), ("BTTS", "N", None): Price(1.90, AT, 11)}
    tips = {t["slot"]: t for t in select_tips(prediction(), prices)}
    assert set(tips) == {"result", "goals", "btts", "builder"}
    assert tips["result"]["legs"] == [("1X2", "1", None)] and tips["result"]["price"] == 1.85
    assert tips["result"]["captured_at"] == AT and tips["result"]["p_source"] == "model"
    assert tips["builder"]["price"] is None and tips["builder"]["fair"] >= 1.50      # never a market price
    g = tips["goals"]
    assert (g["price"] or g["fair"]) >= 1.30
    assert tips["btts"]["legs"][0][1] in ("Y", "N")


def test_p_final_drives_result_and_goals_2_5():
    # The market makes the away side favourite and rates over 2.5 high.
    final = {"1X2|1": {"p": 0.25, "bookmaker": "1xBet", "captured_at": "x"},
             "1X2|X": {"p": 0.25, "bookmaker": "1xBet", "captured_at": "x"},
             "1X2|2": {"p": 0.50, "bookmaker": "1xBet", "captured_at": "x"}}
    tips = {t["slot"]: t for t in select_tips(prediction(final), {})}
    assert tips["result"]["legs"] == [("1X2", "2", None)]
    assert tips["result"]["p_source"] == "market" and tips["result"]["p"] == 0.50
    assert tips["result"]["price"] is None                                         # no 1xBet price: hit rate only


def test_confidence_formula():
    assert confidence(0.80, 1.0) == 10.0
    assert confidence(0.60, 1.0) == 5.0
    assert confidence(0.60, 0.5) == 2.5
    assert confidence(0.35, 1.0) == 0.0


def test_free_fixtures_by_priority_then_kickoff():
    c = [{"fixture_id": 1, "priority": None, "kickoff": datetime(2026, 10, 25, 12)},
         {"fixture_id": 2, "priority": 2, "kickoff": datetime(2026, 10, 25, 18)},
         {"fixture_id": 3, "priority": 1, "kickoff": datetime(2026, 10, 25, 20)},
         {"fixture_id": 4, "priority": 2, "kickoff": datetime(2026, 10, 25, 15)},
         {"fixture_id": 5, "priority": None, "kickoff": datetime(2026, 10, 25, 9)}]
    assert choose_free(c) == [3, 4, 2]
    assert choose_free(c, already=2) == [3]
    assert choose_free(c, already=3) == []


def leg(fid, p, price, slot="result", locks=12, captured=7):
    return {"fixture_id": fid, "slot": slot, "legs": [("1X2", "1", None)], "p": p, "price": price,
            "captured_at": datetime(2026, 10, 25, captured), "locks_at": datetime(2026, 10, 25, locks)}


def test_banker_is_highest_priced_result_or_goals_tip():
    tips = [leg(1, 0.70, 1.40), leg(2, 0.80, 1.25), leg(3, 0.75, 1.35), leg(4, 0.90, None)]
    assert choose_banker(tips)["fixture_id"] == 3                                   # 2 too short, 4 unpriced
    assert choose_banker([leg(9, 0.9, None)]) is None


def test_slips_from_the_whole_day():
    tips = [leg(1, 0.85, 1.30, locks=15, captured=7), leg(1, 0.95, 1.20, slot="goals"),   # same match twice
            leg(2, 0.82, 1.40, locks=13, captured=9), leg(3, 0.70, 1.60, locks=18, captured=8),
            leg(4, 0.65, 1.90, locks=20, captured=7)]
    slips = {s["kind"]: s for s in build_slips(tips)}
    two = slips["odds2"]
    assert [t["fixture_id"] for t in two["legs"]] == [1, 2, 3]                     # one leg per match, by p
    assert two["price"] == pytest.approx(1.20 * 1.40 * 1.60, abs=0.01)
    assert two["chance"] == pytest.approx(0.95 * 0.82 * 0.70)
    assert two["captured_at"] == datetime(2026, 10, 25, 9)                        # latest leg snapshot
    assert two["locks_at"] == datetime(2026, 10, 25, 12)                          # earliest leg lock (match 1's goals tip)
    assert all(t["p"] >= 0.80 for t in slips["low_risk"]["legs"])
    assert len(slips["bankers"]["legs"]) <= 3
