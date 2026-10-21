from engrebodds_ingest.jobs.audit import odds_summary, season_quality, stats_summary, summarise_leagues


def league(lid, name, country, ltype="League", current=True, odds=True, stats=True):
    season = {"year": 2026, "current": current,
              "coverage": {"odds": odds, "fixtures": {"statistics_fixtures": stats}}}
    return {"league": {"id": lid, "name": name, "type": ltype}, "country": {"name": country}, "seasons": [season]}


def fixture(status, ft=(1, 0), ht=(0, 0)):
    return {"fixture": {"status": {"short": status}},
            "score": {"fulltime": {"home": ft[0], "away": ft[1]}, "halftime": {"home": ht[0], "away": ht[1]}}}


def test_allowlist_rule():
    s = summarise_leagues([
        league(39, "Premier League", "England"),
        league(1, "Odds only", "Spain", stats=False),
        league(2, "Old", "Italy", current=False),
        league(399, "NPFL", "Nigeria", odds=False, stats=False),   # pinned despite no coverage
        league(12, "CAF Champions League", "World", ltype="Cup", odds=False),
    ])
    assert s["with_current"] == 4
    assert s["with_odds"] == 2
    assert s["with_odds_and_stats"] == 1
    assert s["allowlist_ids"] == {39, 399, 12}
    assert s["allowlist_by_type"] == {"League": 2, "Cup": 1}


def test_season_quality():
    q = season_quality([fixture("FT"), fixture("AET", ht=(None, None)), fixture("FT", ft=(None, None)),
                        fixture("PST", ft=(None, None)), fixture("CANC", ft=(None, None))])
    assert (q["finished"], q["postponed"], q["void"]) == (3, 1, 1)
    assert (q["finished_missing_ft"], q["finished_missing_ht"]) == (1, 1)


def test_odds_summary():
    resp = [{"bookmakers": [
        {"name": "Bet365", "bets": [{"name": "Match Winner"}, {"name": "Goals Over/Under"}]},
        {"name": "1xBet", "bets": [{"name": "Match Winner"}, {"name": "Corners Over Under"}]},
    ]}]
    o = odds_summary(resp)
    assert o["bookmakers"] == ["1xBet", "Bet365"]
    assert o["markets_total"] == 3
    assert o["key_markets"]["Corners Over Under"] and not o["key_markets"]["Exact Score"]
    assert odds_summary([])["bookmakers"] == []


def test_stats_summary_needs_both_teams():
    resp = [{"statistics": [{"type": "Corner Kicks", "value": 5}, {"type": "Fouls", "value": None}]},
            {"statistics": [{"type": "Corner Kicks", "value": 3}, {"type": "Fouls", "value": 9}]}]
    assert stats_summary(resp) == ["Corner Kicks"]
    assert stats_summary([]) == []
