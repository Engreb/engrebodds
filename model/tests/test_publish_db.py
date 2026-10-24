"""Publishing behind the switch, against the local Supabase database (each test rolled back)."""

from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from engrebodds_ingest.config import load_settings
from engrebodds_model.dixon_coles import score_matrix
from engrebodds_model.live import leagues
from engrebodds_model.live.predict import write
from engrebodds_model.live.publish import _preview, _publish, plan
from engrebodds_model.live.settings import defaults
from engrebodds_model.live.tips import publishing_window
from engrebodds_model.markets import all_markets


@pytest.fixture
def conn():
    try:
        c = psycopg.connect(load_settings().database_url, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("local Supabase is not running")
    yield c
    c.rollback()
    c.close()


def pick_now() -> datetime:
    """A 'now' with room for kick-offs 3-4 hours ahead inside its publishing window (real clock where possible)."""
    real = datetime.now(UTC).replace(tzinfo=None)
    _, end = publishing_window(real)
    return real if end - real > timedelta(hours=5) else end + timedelta(minutes=1)


def setup_league(conn, enabled=True, api_id=990039, quality=1.0, n_fixtures=2, now=None):
    league = conn.execute(
        """insert into public.leagues (api_league_id, name, country, type, tier, tips_enabled)
           values (%s, 'Test League', 'England', 'League', 'full', %s) returning id""", (api_id, enabled)).fetchone()[0]
    bookmaker = conn.execute("select id from public.bookmakers where is_display").fetchone()[0]
    fixtures = []
    for i in range(n_fixtures):
        home, away = [conn.execute("insert into public.teams (api_team_id, name) values (%s, %s) returning id",
                                   (api_id * 10 + 2 * i + j, f"T{api_id}-{i}-{j}")).fetchone()[0] for j in range(2)]
        kickoff = now + timedelta(hours=3 + i / 2)
        fid = conn.execute(
            """insert into public.fixtures (api_fixture_id, league_id, season, kickoff_at, home_team_id, away_team_id)
               values (%s, %s, 2026, %s, %s, %s) returning id""", (api_id * 100 + i, league, kickoff, home, away)).fetchone()[0]
        grid = score_matrix(2.2, 0.6, -0.05)
        probs = {k: round(v, 5) for k, v in all_markets(grid).items()}
        write(conn, [{"fixture_id": fid, "model_version": "dc-untuned", "settings_id": None, "lambda_home": 2.2,
                      "lambda_away": 0.6, "rho": -0.05, "p_home": probs["1X2|1"], "p_draw": probs["1X2|X"],
                      "p_away": probs["1X2|2"], "probs": probs, "final": {}, "cs_top": [], "ht_score": "1-0",
                      "data_quality": quality, "fresh_days": 4}], [], defaults())
        for sel, price in (("1", 1.40), ("X", 4.8), ("2", 8.0)):
            conn.execute("""insert into public.odds_snapshots (fixture_id, bookmaker_id, market_code, selection, price,
                                captured_at) values (%s, %s, '1X2', %s, %s, now())""", (fid, bookmaker, sel, price))
        fixtures.append(fid)
    return league, fixtures


def count(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


def test_preview_mode_never_touches_the_track_record(conn):
    now = pick_now()
    _, fids = setup_league(conn, now=now)
    p = plan(conn, now, preview_all=False)
    assert {f["row"].fixture_id for f in p["fixtures"]} == set(fids)
    notes = []
    _preview(conn, p, notes)
    assert count(conn, "select count(*) from public.tips where fixture_id = any(%s)", fids) == 0
    assert count(conn, "select count(*) from public.tip_previews where kind = 'tip'") == 2
    assert count(conn, "select count(*) from public.tip_previews where kind = 'slip'") >= 1
    payload = conn.execute("select payload from public.tip_previews where kind = 'tip' limit 1").fetchone()[0]
    assert len(payload["tips"]) == 4 and any(t["price_1xbet"] == 1.40 and t["captured_at"] for t in payload["tips"])
    _preview(conn, plan(conn, now, preview_all=False), notes)               # a new preview replaces the old one
    assert count(conn, "select count(*) from public.tip_previews where kind = 'tip'") == 2


def test_on_mode_publishes_once_with_free_banker_and_slips(conn):
    now = pick_now()
    _, fids = setup_league(conn, now=now)
    notes = []
    _publish(conn, plan(conn, now, preview_all=False), now, notes)
    assert count(conn, "select count(*) from public.tips where fixture_id = any(%s)", fids) == 8   # 4 per match
    assert count(conn, "select count(*) from public.tips where fixture_id = any(%s) and is_free", fids) == 8   # 2 < 3 free
    assert count(conn, "select count(*) from public.tips where fixture_id = any(%s) and is_banker", fids) == 1
    priced = conn.execute("""select ref_odds::float, ref_captured_at is not null, p_source from public.tips
                              where fixture_id = %s and slot = 'result'""", (fids[0],)).fetchone()
    assert priced == (1.40, True, "model")
    assert count(conn, "select count(*) from public.tips where fixture_id = any(%s) and slot = 'builder' "
                       "and ref_odds is null", fids) == 2                   # builder: no market price
    start, _ = publishing_window(now)
    slips = count(conn, "select count(*) from public.slips where slip_date = %s", start.date())
    assert slips >= 1
    assert count(conn, "select count(*) from public.slips where slip_date = %s and ref_captured_at is null",
                 start.date()) == 0
    # A later run in the same window: nothing new to tip, and no second set of slips.
    later = plan(conn, now + timedelta(minutes=30), preview_all=False)
    assert later["fixtures"] == []
    _publish(conn, later, now + timedelta(minutes=30), notes)
    assert count(conn, "select count(*) from public.slips where slip_date = %s", start.date()) == slips


def test_ineligible_fixtures_are_skipped_unless_previewing_all(conn):
    now = pick_now()
    setup_league(conn, enabled=False, api_id=990041, now=now)
    setup_league(conn, enabled=True, api_id=990042, quality=0.4, now=now)
    p = plan(conn, now, preview_all=False)
    assert p["fixtures"] == []
    assert p["skipped"] == {"league not enabled": 2, "data quality below 0.6": 2}
    assert len(plan(conn, now, preview_all=True)["fixtures"]) == 4


def test_nothing_beyond_the_window(conn):
    now = pick_now()
    _, end = publishing_window(now)
    _, fids = setup_league(conn, now=end + timedelta(hours=1))             # tomorrow's fixtures
    assert plan(conn, now, preview_all=False)["fixtures"] == []


def test_league_switch_refuses_npfl_and_unproven_leagues(conn):
    refused = leagues.league_tips(conn, 399, True)
    assert not refused["changed"] and "NPFL" in refused["refused"]
    conn.execute("""insert into public.leagues (api_league_id, name, country, type, tier)
                    values (990050, 'New League', 'Spain', 'League', 'full')""")
    result = leagues.league_tips(conn, 990050, True)
    assert not result["changed"] and "only 0 matches" in result["refused"]
    off = leagues.league_tips(conn, 990050, False)
    assert off["changed"] and not count(conn, "select tips_enabled from public.leagues where api_league_id = 990050")
    assert leagues.league_priority(conn, 990050, 2) == "New League"
    assert count(conn, "select display_order from public.leagues where api_league_id = 990050") == 2


def test_published_tips_are_graded_end_to_end(conn):
    now = pick_now()
    _, fids = setup_league(conn, now=now, n_fixtures=1)
    _publish(conn, plan(conn, now, preview_all=False), now, [])
    conn.execute("update public.fixtures set status = 'FT', home_90 = 2, away_90 = 0, home_ht = 1, away_ht = 0 "
                 "where id = %s", (fids[0],))
    conn.execute("select public.grade_all(now() + interval '6 hours')")
    results = dict(conn.execute("select slot, result from public.tips where fixture_id = %s", (fids[0],)).fetchall())
    assert results["result"] == "won" and set(results.values()) <= {"won", "lost"}
    row = conn.execute("""select odds_source, profit_units::float from public.track_record r
                           join public.tips t on t.id = r.tip_id where t.fixture_id = %s and t.slot = 'result'""",
                       (fids[0],)).fetchone()
    assert row == ("bookmaker", pytest.approx(0.40))
