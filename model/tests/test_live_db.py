"""Live prediction storage rules against the local Supabase database (each test rolled back)."""

from datetime import UTC, datetime

import psycopg
import pytest

from engrebodds_ingest.config import load_settings
from engrebodds_model.live import settings as live_settings
from engrebodds_model.live.predict import write
from engrebodds_model.live.settings import defaults


@pytest.fixture
def conn():
    try:
        c = psycopg.connect(load_settings().database_url, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("local Supabase is not running")
    yield c
    c.rollback()
    c.close()


def fixture(conn, hours_ahead: float) -> int:
    league = conn.execute("select id from public.leagues where api_league_id = 399").fetchone()[0]
    teams = [conn.execute("insert into public.teams (api_team_id, name) values (%s, %s) returning id",
                          (970000 + i, f"Test {i}")).fetchone()[0] for i in range(2)]
    return conn.execute(
        """insert into public.fixtures (api_fixture_id, league_id, season, kickoff_at, home_team_id, away_team_id)
           values (979001, %s, 2027, now() + make_interval(mins => %s), %s, %s) returning id""",
        (league, int(hours_ahead * 60), *teams)).fetchone()[0]


def row(fid: int, p_home=0.5) -> dict:
    return {"fixture_id": fid, "model_version": "dc-untuned", "settings_id": None, "lambda_home": 1.5,
            "lambda_away": 1.1, "rho": -0.05, "p_home": p_home, "p_draw": 0.27, "p_away": 0.73 - p_home,
            "probs": {"1X2|1": p_home, "1X2|X": 0.27, "1X2|2": 0.73 - p_home, "OU|O|2.5": 0.52, "OU|U|2.5": 0.48},
            "final": {"1X2|1": {"p": 0.46, "bookmaker": "1xBet", "captured_at": datetime.now(UTC).isoformat()},
                      "1X2|X": {"p": 0.28, "bookmaker": "1xBet", "captured_at": datetime.now(UTC).isoformat()},
                      "1X2|2": {"p": 0.26, "bookmaker": "1xBet", "captured_at": datetime.now(UTC).isoformat()}},
            "cs_top": [{"score": "1-1", "p": 0.12}], "ht_score": "0-0", "data_quality": 1.0, "fresh_days": 6}


def test_write_upserts_and_view_shows_p_model_and_p_final(conn):
    fid = fixture(conn, 5)
    write(conn, [row(fid)], [], defaults())
    write(conn, [row(fid, p_home=0.55)], [], defaults())                 # re-run updates, no duplicate
    assert conn.execute("select count(*), max(p_home)::float from public.predictions where fixture_id = %s",
                        (fid,)).fetchone() == (1, pytest.approx(0.55))
    view = dict(conn.execute(
        """select market_key, (p_model, p_final, p_source, bookmaker) from public.prediction_markets
            where fixture_id = %s""", (fid,)).fetchall())
    assert view["1X2|1"][2] == "market" and view["1X2|1"][3] == "1xBet" and float(view["1X2|1"][1]) == 0.46
    assert view["OU|O|2.5"][2] == "model" and view["OU|O|2.5"][0] == view["OU|O|2.5"][1]


def test_predictions_freeze_inside_the_lock_but_can_be_thinned(conn):
    fid = fixture(conn, 5)
    write(conn, [row(fid)], [], defaults())
    conn.execute("update public.fixtures set kickoff_at = now() + interval '30 minutes' where id = %s", (fid,))
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            conn.execute("update public.predictions set p_home = 0.9 where fixture_id = %s", (fid,))
    conn.execute("update public.predictions set probs = '{}'::jsonb, final = '{}'::jsonb where fixture_id = %s", (fid,))
    assert conn.execute("select probs from public.predictions where fixture_id = %s", (fid,)).fetchone()[0] == {}


def test_no_new_prediction_inside_the_lock(conn):
    fid = fixture(conn, 0.5)
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            write(conn, [row(fid)], [], defaults())


def test_thin_predictions_keeps_headline_columns(conn):
    fid = fixture(conn, 5)
    write(conn, [row(fid)], [], defaults())
    conn.execute("alter table public.predictions disable trigger predictions_guard")   # move the match into the past
    conn.execute("update public.fixtures set kickoff_at = now() - interval '40 days' where id = %s", (fid,))
    conn.execute("alter table public.predictions enable trigger predictions_guard")
    assert conn.execute("select public.thin_predictions()").fetchone()[0] >= 1
    assert conn.execute("select probs, final, p_home::float from public.predictions where fixture_id = %s",
                        (fid,)).fetchone() == ({}, {}, pytest.approx(0.5))


def test_settings_saved_inactive_and_one_active(conn):
    tuned = {"source": "API-Football (licensed); never football-data", "config": {"xi": 0.003, "penalty": 3.0},
             "calibrator": {"maps": {}, "draw_shift": 0.0}, "validated_on": ["2024", "2025"],
             "ladder": [{"experiment": "x", "accepted": False}]}
    a = live_settings.save(conn, tuned)
    b = live_settings.save(conn, tuned)
    assert conn.execute("select count(*) from public.model_settings where is_active").fetchone()[0] == 0
    assert live_settings.active(conn).version == "dc-untuned"           # nothing active: defaults
    live_settings.activate(conn, a)
    live_settings.activate(conn, b)
    active = conn.execute("select id from public.model_settings where is_active").fetchall()
    assert active == [(b,)]
    assert live_settings.active(conn).config.xi == 0.003
    with pytest.raises(ValueError):
        live_settings.activate(conn, 999_999)


def test_access_rules(conn):
    conn.execute("insert into public.shadow_reports (week_start, results) values (current_date, '{}'::jsonb)")
    conn.execute("set local role anon")
    assert conn.execute("select count(*) from public.shadow_reports").fetchone()[0] == 0   # hidden
    conn.execute("select count(*) from public.model_settings").fetchone()                 # readable
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("insert into public.model_settings (version, source, config) values ('x', 'x', '{}')")
