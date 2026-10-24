from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from engrebodds_model.live.predict import build, market_final
from engrebodds_model.live.settings import DEFAULTS, defaults, from_parts
from engrebodds_model.live.shadow import evaluate, score

NOW = pd.Timestamp("2026-10-24 12:00")


def history(seed=3):
    """Two domestic leagues in one country (ids 10 and 11), three seasons each, team ids 100-107 and 200-207."""
    rng = np.random.default_rng(seed)
    rows = []
    for league, base in (("10", 100), ("11", 200)):
        teams = list(range(base, base + 8))
        for w in range(3 * 40):
            date = NOW - pd.Timedelta(days=7 * (120 - w))
            season = str(date.year if date.month >= 7 else date.year - 1)
            order = rng.permutation(teams)
            for k in range(0, 8, 2):
                rows.append({"league": league, "country": "England", "season": season, "date": date,
                             "home": int(order[k]), "away": int(order[k + 1]), "hg": int(rng.poisson(1.5)),
                             "ag": int(rng.poisson(1.1)), "hthg": 0, "htag": 0, "hst": None, "ast": None})
    return pd.DataFrame(rows)


def upcoming():
    k = NOW + pd.Timedelta(hours=6)
    rows = [
        (1, "10", "League", "England", "League A", k, 100, 101),     # league match: predicted
        (2, "99", "Cup", "England", "FA Cup", k, 102, 103),          # cup, both teams in league 10: predicted
        (3, "99", "Cup", "England", "FA Cup", k, 104, 205),          # cup, mixed leagues: skipped
        (4, "12", "Cup", "World", "UEFA Champions League", k, 100, 200),   # continental: skipped
        (5, "10", "League", "England", "League A", k, 100, 999),     # unknown team: skipped
        (6, "13", "League", "Spain", "Liga X", k, 300, 301),         # league without history: skipped
    ]
    return pd.DataFrame(rows, columns=["fixture_id", "league", "type", "country", "league_name", "kickoff", "home", "away"])


ODDS = {1: {("1X2", "1", None): (2.10, datetime(2026, 10, 24, 10), "1xBet"),
            ("1X2", "X", None): (3.40, datetime(2026, 10, 24, 10), "1xBet"),
            ("1X2", "2", None): (3.60, datetime(2026, 10, 24, 10), "1xBet"),
            ("OU", "O", 2.5): (1.95, datetime(2026, 10, 24, 10), "1xBet")}}       # under 2.5 missing


def test_defaults_are_untuned_and_not_from_football_data():
    s = defaults()
    assert s.version == "dc-untuned" and s.id is None
    assert s.config.xi == 0.0019 and s.config.penalty == 1.0 and s.config.shots_weight == 0.0
    assert s.calibrator.maps == {} and "not tuned" in DEFAULTS["source"]
    assert from_parts(5, "v", "src", {"xi": 0.003, "unknown": 1}, {"maps": {}, "draw_shift": 0}).config.xi == 0.003


def test_market_final_needs_every_selection():
    final = market_final(ODDS[1])
    assert set(final) == {"1X2|1", "1X2|X", "1X2|2"}                 # O/U 2.5 incomplete: model only
    assert sum(v["p"] for v in final.values()) == pytest.approx(1.0, abs=1e-4)
    assert final["1X2|1"]["bookmaker"] == "1xBet" and final["1X2|1"]["captured_at"].startswith("2026-10-24")
    assert market_final(None) == {}


def test_build_predicts_leagues_and_same_league_cups_only():
    rows, runs, notes = build(history(), upcoming(), ODDS, defaults(), NOW)
    by_id = {r["fixture_id"]: r for r in rows}
    assert set(by_id) == {1, 2}
    assert (notes["skipped_mixed_cup"], notes["skipped_world"], notes["skipped_unrated"],
            notes["skipped_league_history"]) == (1, 1, 1, 1)
    assert notes["market_final"] == 1 and notes["mixed_cup_examples"]
    r = by_id[1]
    assert r["model_version"] == "dc-untuned" and r["settings_id"] is None
    assert r["p_home"] + r["p_draw"] + r["p_away"] == pytest.approx(1.0, abs=1e-4)
    assert r["probs"]["1X2|1"] == r["p_home"]                       # p_model
    assert r["final"]["1X2|1"]["p"] != r["probs"]["1X2|1"]          # p_final from the market
    assert by_id[2]["final"] == {}                                    # no odds: p_final = p_model
    assert all(0 <= x["data_quality"] <= 1.0 for x in rows)
    assert all(0 <= x["fresh_days"] < 30 for x in rows)
    assert {run["league_id"] for run in runs} == {10, 11}


def finished(n=120, seed=5):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        p = rng.dirichlet([4, 2.5, 3])
        rows.append({"fixture_id": i, "league": "10", "league_name": "League A",
                     "kickoff": NOW - pd.Timedelta(days=int(i % 20)), "hg": int(rng.poisson(1.5)),
                     "ag": int(rng.poisson(1.1)), "model_version": "dc-untuned", "settings_id": None,
                     "p_home": p[0], "p_draw": p[1], "p_away": p[2],
                     "probs": {"OU|O|2.5": 0.5, "BTTS|Y": 0.52}, "final": {}, "data_quality": 1.0, "fresh_days": 7})
    return pd.DataFrame(rows)


def test_shadow_report_structure():
    df = finished()
    closing = pd.DataFrame({"fixture_id": [0, 1, 2], "bookmaker": ["1xBet"] * 3,
                            "price_h": [2.0, 1.8, 2.5], "price_d": [3.3, 3.5, 3.1], "price_a": [3.8, 4.5, 2.9]})
    hist = history()
    s = score(df, closing, hist)
    assert s["matches"] == 120 and s["with_market_p_final"] == 0
    assert s["log_loss_model"] == s["log_loss_final"]                # no market finals: identical
    assert s["closing"]["matches"] == 3 and s["closing"]["bookmakers"] == {"1xBet": 3}
    assert "OU|O|2.5" in s["goals_calibration"]
    report = evaluate(df, closing, hist, NOW - pd.Timedelta(days=7))
    assert set(report["sections"]) == {"week", "to_date"}
    assert report["sections"]["to_date"]["by_league"]["League A"]["matches"] == 120
    assert "Free API plan" in report["caveat"]
