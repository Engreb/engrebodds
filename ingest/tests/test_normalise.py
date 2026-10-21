import json
from decimal import Decimal
from pathlib import Path

import pytest

from engrebodds_ingest.normalise import (
    BETS, fixture_issues, fixture_row, odds_rows, team_stats_rows,
)

FIXTURES = Path(__file__).parent / "fixtures"
STORED = {8, 11, 4}   # Bet365, 1xBet, Pinnacle


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def europa_odds():
    return odds_rows(load("odds_europa_league.json")["response"][0], STORED)


def find(rows, bookmaker, market, selection, line=None):
    line = None if line is None else Decimal(str(line))
    return next(r.price for r in rows
                if (r.api_bookmaker_id, r.market_code, r.selection, r.line) == (bookmaker, market, selection, line))


# ---------- odds ----------

def test_only_stored_bookmakers(europa_odds):
    assert {r.api_bookmaker_id for r in europa_odds} == STORED   # the sample's 4th bookmaker (id 6) is dropped


def test_result_and_goals_markets(europa_odds):
    assert find(europa_odds, 8, "1X2", "1") == Decimal("6.25")
    assert find(europa_odds, 8, "1X2", "2") == Decimal("1.50")
    assert find(europa_odds, 8, "OU", "O", 2.5) == Decimal("1.57")
    assert find(europa_odds, 8, "OU", "U", 1.5) == Decimal("5.00")
    assert find(europa_odds, 8, "TEAM_OU", "HO", 1.5) == Decimal("3.50")
    assert find(europa_odds, 8, "CORNERS_OU", "O", 9.5) == Decimal("1.83")


def test_correct_score_and_clean_sheet(europa_odds):
    assert find(europa_odds, 8, "CS", "2-1") == Decimal("19.00")
    assert find(europa_odds, 8, "CLEAN_SHEET", "H") == Decimal("8.00")
    # Only the "Yes" side of clean sheet exists in our catalogue.
    assert not [r for r in europa_odds if r.market_code == "CLEAN_SHEET" and r.selection not in ("H", "A")]


def test_lines_are_whole_or_half(europa_odds):
    assert all(r.line is None or (r.line * 2) % 1 == 0 for r in europa_odds)


def test_every_mapped_selection_is_valid_for_its_market(europa_odds):
    valid = {
        "1X2": {"1", "X", "2"}, "HT_1X2": {"1", "X", "2"}, "DC": {"1X", "X2", "12"},
        "OU": {"O", "U"}, "HT_OU": {"O", "U"}, "CORNERS_OU": {"O", "U"}, "CARDS_OU": {"O", "U"},
        "TEAM_OU": {"HO", "HU", "AO", "AU"}, "BTTS": {"Y", "N"}, "CLEAN_SHEET": {"H", "A"},
    }
    for r in europa_odds:
        if r.market_code in ("CS", "HT_CS"):
            h, a = r.selection.split("-")
            assert h.isdigit() and a.isdigit()
        else:
            assert r.selection in valid[r.market_code], r


def test_unmapped_bets_and_quarter_lines_skipped():
    item = {"fixture": {"id": 1}, "bookmakers": [{"id": 8, "bets": [
        {"id": 50, "name": "Goal Line", "values": [{"value": "Over 2.25", "odd": "1.9"}]},
        {"id": 5, "name": "Goals Over/Under", "values": [
            {"value": "Over 2.25", "odd": "1.9"}, {"value": "Over 3", "odd": "2.4"}, {"value": "Over 2.5", "odd": "1.0"}]},
    ]}]}
    rows = odds_rows(item, {8})
    assert [(r.market_code, r.line, r.price) for r in rows] == [("OU", Decimal("3"), Decimal("2.4"))]


def test_bet_ids_match_api_names():
    # Guards against a typo in BETS: ids checked against the names in the recorded response.
    names = {b["id"]: b["name"] for bk in load("odds_europa_league.json")["response"][0]["bookmakers"] for b in bk["bets"]}
    expected = {1: "Match Winner", 12: "Double Chance", 5: "Goals Over/Under", 16: "Total - Home",
                17: "Total - Away", 8: "Both Teams Score", 27: "Clean Sheet - Home", 28: "Clean Sheet - Away",
                10: "Exact Score", 13: "First Half Winner", 6: "Goals Over/Under First Half",
                31: "Correct Score - First Half", 45: "Corners Over Under", 80: "Cards Over/Under"}
    assert set(BETS) == set(expected)
    for bet_id, name in expected.items():
        if bet_id in names:
            assert names[bet_id] == name


# ---------- fixtures ----------

@pytest.fixture(scope="module")
def fixtures():
    return {r.api_fixture_id: r for r in map(fixture_row, load("fixtures_sample.json")["response"])}


def test_fixture_fields(fixtures):
    caf = fixtures[1644171]
    assert (caf.api_league_id, caf.season, caf.status) == (12, 2026, "FT")
    assert (caf.home_90, caf.away_90) == (1, 6)
    assert caf.home.name == "15 de Agosto" and caf.kickoff_at.tzinfo is not None
    upcoming = fixtures[1636215]
    assert upcoming.status == "NS" and upcoming.home_90 is None


def test_extra_time_kept_apart_from_90_minutes(fixtures):
    aet = fixtures[1645195]
    assert (aet.home_90, aet.away_90, aet.home_et, aet.away_et) == (1, 1, 0, 1)
    assert fixture_issues(aet) == []


def test_penalties(fixtures):
    pen = fixtures[1646585]
    assert (pen.home_pen, pen.away_pen) == (4, 3)
    assert fixture_issues(pen) == []


def test_inconsistent_score_flagged(fixtures):
    # Recorded as decided on penalties, but 1-0 after 90 minutes and no penalty score.
    issues = fixture_issues(fixtures[1639281])
    assert any("not level" in i for i in issues)
    assert any("penalty score" in i for i in issues)


# ---------- statistics ----------

def test_stats_null_means_zero():
    home, away = team_stats_rows(load("stats_epl.json")["response"])
    assert (home.api_team_id, home.corners, home.yellow_cards, home.red_cards, home.fouls) == (65, 7, 2, 0, 10)
    assert (home.shots_total, home.shots_on_target) == (10, 2)
    assert home.possession == Decimal("52") and home.xg == Decimal("1.20")
    assert away.corners == 4


def test_stats_missing_type_stays_unknown():
    home, _ = team_stats_rows(load("stats_caf.json")["response"])
    assert home.corners == 2 and home.yellow_cards == 2
    assert home.fouls is None     # CAF data has no fouls: grading waits, then voids
    assert home.xg is None
