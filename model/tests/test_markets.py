import re

import numpy as np
import pytest

from engrebodds_model.dixon_coles import score_matrix
from engrebodds_model.markets import (
    all_markets, combo_prob, fair_odds, ht_matrix, key, prob, selection_mask, top_scores,
)

FT = score_matrix(1.6, 1.1, -0.05)

# Selections allowed per market code, mirroring public.valid_selection() in the Phase 1 migrations.
VALID = {
    "1X2": {"1", "X", "2"}, "HT_1X2": {"1", "X", "2"}, "DC": {"1X", "X2", "12"},
    "OU": {"O", "U"}, "HT_OU": {"O", "U"}, "TEAM_OU": {"HO", "HU", "AO", "AU"},
    "BTTS": {"Y", "N"}, "CLEAN_SHEET": {"H", "A"},
}


def test_result_markets_add_up():
    p = [prob(FT, "1X2", s) for s in ("1", "X", "2")]
    assert sum(p) == pytest.approx(1.0)
    assert prob(FT, "DC", "1X") == pytest.approx(p[0] + p[1])
    assert prob(FT, "DC", "12") == pytest.approx(p[0] + p[2])


def test_over_under_and_push():
    for line in (0.5, 1.5, 2.5, 3.5, 4.5):
        assert prob(FT, "OU", "O", line) + prob(FT, "OU", "U", line) == pytest.approx(1.0)
    exactly_two = sum(FT[i, 2 - i] for i in range(3))
    assert prob(FT, "OU", "O", 2.0) + prob(FT, "OU", "U", 2.0) + exactly_two == pytest.approx(1.0)


def test_team_goals_btts_clean_sheets():
    assert prob(FT, "TEAM_OU", "HO", 0.5) == pytest.approx(1 - FT[0, :].sum())
    assert prob(FT, "CLEAN_SHEET", "H") == pytest.approx(FT[:, 0].sum())
    assert prob(FT, "BTTS", "Y") + prob(FT, "BTTS", "N") == pytest.approx(1.0)
    assert prob(FT, "BTTS", "Y") == pytest.approx(FT[1:, 1:].sum())


def test_correct_score():
    assert prob(FT, "CS", "1-1") == pytest.approx(FT[1, 1])
    top = top_scores(FT)
    assert len(top) == 5 and top[0][1] == pytest.approx(FT.max())
    assert [p for _, p in top] == sorted((p for _, p in top), reverse=True)


def test_combo_is_exact_joint_probability():
    legs = [("DC", "1X", None), ("OU", "O", 1.5)]
    manual = FT[selection_mask("DC", "1X", None, 11) & selection_mask("OU", "O", 1.5, 11)].sum()
    combo = combo_prob(FT, legs)
    assert combo == pytest.approx(manual)
    assert combo <= min(prob(FT, "DC", "1X"), prob(FT, "OU", "O", 1.5))
    # Legs aren't independent, so the joint probability differs from the product.
    assert combo != pytest.approx(prob(FT, "DC", "1X") * prob(FT, "OU", "O", 1.5), rel=1e-3)


def test_half_time_matrix():
    ht = ht_matrix(1.6, 1.1, 0.45)
    assert ht.sum() == pytest.approx(1.0)
    assert ht[0, 0] == pytest.approx(np.exp(-(1.6 + 1.1) * 0.45), rel=1e-6)
    assert prob(ht, "HT_1X2", "X") > prob(FT, "1X2", "X")       # draws are commoner at half-time


def test_all_markets_keys_match_the_catalogue():
    p = all_markets(FT, ht_matrix(1.6, 1.1, 0.45))
    assert p["OU|O|2.5"] == pytest.approx(prob(FT, "OU", "O", 2.5))
    for k, v in p.items():
        market, selection, *line = k.split("|")
        assert 0.0 <= v <= 1.0
        if market in ("CS", "HT_CS"):
            assert re.fullmatch(r"\d{1,2}-\d{1,2}", selection)
        else:
            assert selection in VALID[market], k
        assert bool(line) == (market in ("OU", "HT_OU", "TEAM_OU")), k


def test_key_format():
    assert key("OU", "O", 2.5) == "OU|O|2.5"
    assert key("BTTS", "Y") == "BTTS|Y"
    assert key("OU", "U", 3.0) == "OU|U|3"


def test_fair_odds_include_six_percent_margin():
    assert fair_odds(0.5) == 1.88
    assert fair_odds(0.25) == 3.76
    assert fair_odds(0.99) == 1.01          # never below 1.01
    with pytest.raises(ValueError):
        fair_odds(0.0)


def test_invalid_selection_rejected():
    with pytest.raises(ValueError):
        prob(FT, "1X2", "H")
    with pytest.raises(ValueError):
        prob(FT, "CORNERS_OU", "O", 9.5)    # stats markets aren't goals markets
