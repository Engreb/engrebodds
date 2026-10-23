"""Job logic against the local Supabase database, using recorded API responses.

Each test runs in a transaction that is rolled back. Skipped when the local database isn't running.
"""

import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

from engrebodds_ingest.config import load_settings
from engrebodds_ingest.db import upsert_fixtures, upsert_team_stats
from engrebodds_ingest.jobs.sync_odds import store_odds
from engrebodds_ingest.jobs.sync_reference import apply_leagues
from engrebodds_ingest.jobs.sync_standings import apply_standings
from engrebodds_ingest.normalise import fixture_row, team_stats_rows

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def conn():
    try:
        c = psycopg.connect(load_settings().database_url, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("local Supabase is not running")
    yield c
    c.rollback()
    c.close()


def league(api_id, name, country="World", odds=True, stats=True, ltype="Cup"):
    return {"league": {"id": api_id, "name": name, "type": ltype, "logo": None},
            "country": {"name": country, "code": None},
            "seasons": [{"year": 2025, "current": False, "coverage": {}},
                        {"year": 2026, "current": True, "start": "2026-08-01", "end": "2027-05-31",
                         "coverage": {"odds": odds, "standings": True,
                                      "fixtures": {"statistics_fixtures": stats}}}]}


# Leagues of the recorded fixtures: Europa League (3) and CAF CL (12) full, English FA Cup (45) goals,
# Irish League Cup (559) goals, Thai FA Cup (298) off. NPFL (399) is pinned but has no coverage here.
LEAGUES = [league(3, "UEFA Europa League"), league(12, "CAF Champions League"),
           league(45, "FA Cup", "England", stats=False), league(559, "League Cup", "Northern-Ireland", stats=False),
           league(298, "FA Cup", "Thailand", odds=False, stats=False),
           league(399, "NPFL", "Nigeria", odds=False, stats=False, ltype="League")]


def tiers(conn):
    return dict(conn.execute("select api_league_id, tier from public.leagues where api_league_id = any(%s)",
                             ([3, 12, 45, 559, 298, 399],)).fetchall())


def seed(conn):
    apply_leagues(conn, LEAGUES)
    rows = [fixture_row(x) for x in load("fixtures_sample.json")["response"]]
    now = max(r.kickoff_at for r in rows) + timedelta(days=1)
    return rows, now


def test_league_tiers_and_pinning(conn):
    apply_leagues(conn, LEAGUES)
    assert tiers(conn) == {3: "full", 12: "full", 45: "goals", 559: "goals", 298: "off", 399: "goals"}
    seasons = {r[0] for r in conn.execute("select s.season from public.league_seasons s join public.leagues l "
                                          "on l.id = s.league_id where l.api_league_id = 3")}
    assert {2025, 2026} <= seasons


def test_fixtures_upsert_is_idempotent_and_skips_off_leagues(conn):
    rows, now = seed(conn)
    ids, notes = upsert_fixtures(conn, rows, now)
    assert set(ids) == {1636215, 1644171, 1646585, 1639281}          # Thai FA Cup (off) skipped
    count = conn.execute("select count(*) from public.fixtures").fetchone()[0]
    ids2, _ = upsert_fixtures(conn, rows, now)
    assert ids2 == ids
    assert conn.execute("select count(*) from public.fixtures").fetchone()[0] == count
    assert len(notes) == 1 and "1639281" in notes[0]


def test_flagged_score_and_manual_correction(conn):
    rows, now = seed(conn)
    upsert_fixtures(conn, rows, now)
    issue = conn.execute("select score_issue from public.fixtures where api_fixture_id = 1639281").fetchone()[0]
    assert issue == "PEN without a penalty score"
    # Someone checks the match, corrects it and locks the score; the next sync must not undo that.
    conn.execute("""update public.fixtures set home_90 = 1, away_90 = 1, home_pen = 4, away_pen = 5,
                    score_issue = null, manual_score = true where api_fixture_id = 1639281""")
    upsert_fixtures(conn, rows, now)
    assert conn.execute("select home_90, away_90, score_issue from public.fixtures "
                        "where api_fixture_id = 1639281").fetchone() == (1, 1, None)


def test_extra_time_stored_apart(conn):
    rows, now = seed(conn)
    upsert_fixtures(conn, rows, now)
    assert conn.execute("select status, home_90, away_90, home_et, away_et, home_pen, away_pen, finished_at is not null "
                        "from public.fixtures where api_fixture_id = 1646585").fetchone() == \
        ("PEN", 1, 1, 1, 1, 4, 3, True)


def test_team_stats(conn):
    rows, now = seed(conn)
    ids, _ = upsert_fixtures(conn, rows, now)
    assert upsert_team_stats(conn, ids[1644171], team_stats_rows(load("stats_caf.json")["response"])) == 2
    corners, fouls = conn.execute("select sum(corners), max(fouls) from public.fixture_team_stats "
                                  "where fixture_id = %s", (ids[1644171],)).fetchone()
    assert corners > 0 and fouls is None
    assert conn.execute("select stats_fetched_at is not null from public.fixtures where id = %s",
                        (ids[1644171],)).fetchone()[0]


def test_odds_changed_prices_only(conn):
    rows, _ = seed(conn)
    europa = next(r for r in rows if r.api_fixture_id == 1636215)
    before_ko = europa.kickoff_at - timedelta(hours=6)
    ids, _ = upsert_fixtures(conn, rows, before_ko)
    fid = ids[1636215]
    conn.execute("delete from public.odds_snapshots where fixture_id = %s", (fid,))   # start with no history
    item = load("odds_europa_league.json")["response"][0]

    first = store_odds(conn, [item], before_ko)
    assert first > 100
    by_book = dict(conn.execute("""select b.name, count(distinct o.market_code) from public.odds_snapshots o
                                   join public.bookmakers b on b.id = o.bookmaker_id
                                   where o.fixture_id = %s group by b.name""", (fid,)).fetchall())
    assert by_book["Bet365"] > by_book["Pinnacle"] and by_book["Pinnacle"] <= 4   # lean policy
    assert conn.execute("select count(*) from public.odds_snapshots o join public.bookmakers b on b.id = o.bookmaker_id "
                        "where o.fixture_id = %s and o.market_code = 'CS' and b.name <> 'Bet365'",
                        (fid,)).fetchone()[0] == 0

    assert store_odds(conn, [item], before_ko + timedelta(hours=1)) == 0   # nothing changed

    # Bet365 shortens the away win from 1.50 to 1.45: one new row, shown as moving down.
    bet365 = next(bk for bk in item["bookmakers"] if bk["id"] == 8)
    winner = next(b for b in bet365["bets"] if b["id"] == 1)
    next(v for v in winner["values"] if v["value"] == "Away")["odd"] = "1.45"
    assert store_odds(conn, [item], before_ko + timedelta(hours=2)) == 1
    assert conn.execute("""select l.price, l.movement from public.odds_latest l join public.bookmakers b on b.id = l.bookmaker_id
                           where l.fixture_id = %s and b.name = 'Bet365'
                             and l.market_code = '1X2' and l.selection = '2'""", (fid,)).fetchone() == \
        (Decimal("1.45"), "down")

    assert store_odds(conn, [item], europa.kickoff_at + timedelta(minutes=5)) == 0   # nothing after kick-off


def test_standings_replace(conn):
    apply_leagues(conn, LEAGUES)
    league_id = conn.execute("select id from public.leagues where api_league_id = 399").fetchone()[0]

    def entry(team_id, name, rank, pts):
        return {"rank": rank, "team": {"id": team_id, "name": name, "logo": None}, "points": pts, "group": "NPFL",
                "form": "WWD", "all": {"played": 3, "win": 2, "draw": 1, "lose": 0, "goals": {"for": 5, "against": 1}}}

    resp = [{"league": {"standings": [[entry(910001, "Enyimba", 1, 7), entry(910002, "Rangers", 2, 6)]]}}]
    assert apply_standings(conn, league_id, 2027, resp) == 2
    resp[0]["league"]["standings"][0].pop()
    assert apply_standings(conn, league_id, 2027, resp) == 1
    assert conn.execute("select count(*) from public.standings where league_id = %s", (league_id,)).fetchone()[0] == 1
