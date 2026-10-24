"""Read live inputs from Supabase into the backtest's DataFrame shape.

Teams are identified by their database ids (names can repeat across leagues); `league` is the league id
as text. Continental and international competitions (API-Football country "World") are excluded.
"""

import pandas as pd
import psycopg

TRAINING_SQL = """
select f.league_id::text as league, l.country, f.season::text as season,
       (f.kickoff_at at time zone 'UTC') as date, f.home_team_id as home, f.away_team_id as away,
       f.home_90 as hg, f.away_90 as ag, f.home_ht as hthg, f.away_ht as htag,
       sh.shots_on_target as hst, sa.shots_on_target as ast
  from public.fixtures f
  join public.leagues l on l.id = f.league_id
  left join public.fixture_team_stats sh on sh.fixture_id = f.id and sh.team_id = f.home_team_id
  left join public.fixture_team_stats sa on sa.fixture_id = f.id and sa.team_id = f.away_team_id
 where f.status in ('FT', 'AET', 'PEN') and f.home_90 is not null
   and l.tier <> 'off' and l.type = 'League' and l.country <> 'World'
   and f.kickoff_at >= %(since)s and f.kickoff_at < %(until)s
 order by f.kickoff_at
"""

UPCOMING_SQL = """
select f.id as fixture_id, f.league_id::text as league, l.type, l.country, l.name as league_name,
       (f.kickoff_at at time zone 'UTC') as kickoff, f.home_team_id as home, f.away_team_id as away
  from public.fixtures f
  join public.leagues l on l.id = f.league_id
 where l.tier <> 'off'
   and f.status in ('NS', 'TBD')
   and f.kickoff_at > now() + interval '1 hour'
   and f.kickoff_at <= now() + make_interval(hours => %(hours)s)
 order by f.kickoff_at
"""

# Latest prices from the displayed bookmaker (1xBet) for the markets that get market-based p_final.
DISPLAY_ODDS_SQL = """
select o.fixture_id, o.market_code, o.selection, o.line, o.price, o.captured_at, b.name as bookmaker
  from public.odds_latest o
  join public.bookmakers b on b.id = o.bookmaker_id and b.is_display
 where o.fixture_id = any(%(ids)s)
   and (o.market_code = '1X2' or (o.market_code = 'OU' and o.line = 2.5))
"""

FROZEN_SQL = """
select p.fixture_id, f.league_id::text as league, l.name as league_name, (f.kickoff_at at time zone 'UTC') as kickoff,
       f.home_90 as hg, f.away_90 as ag, p.model_version, p.settings_id, p.p_home, p.p_draw, p.p_away,
       p.probs, p.final, p.data_quality, p.fresh_days
  from public.predictions p
  join public.fixtures f on f.id = p.fixture_id
  join public.leagues l on l.id = f.league_id
 where f.status in ('FT', 'AET', 'PEN') and f.home_90 is not null
 order by f.kickoff_at
"""

# Closing 1X2 prices, best available benchmark per fixture: Pinnacle, then 1xBet, then Bet365.
CLOSING_SQL = """
select distinct on (c.fixture_id) c.fixture_id, b.name as bookmaker,
       max(c.price) filter (where c.selection = '1') over w as price_h,
       max(c.price) filter (where c.selection = 'X') over w as price_d,
       max(c.price) filter (where c.selection = '2') over w as price_a
  from public.odds_closing c
  join public.bookmakers b on b.id = c.bookmaker_id
 where c.market_code = '1X2' and c.fixture_id = any(%(ids)s)
window w as (partition by c.fixture_id, c.bookmaker_id)
 order by c.fixture_id, case b.name when 'Pinnacle' then 0 when '1xBet' then 1 when 'Bet365' then 2 else 3 end
"""


def _frame(conn, sql: str, params: dict) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d.name for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


def training(conn: psycopg.Connection, since, until) -> pd.DataFrame:
    df = _frame(conn, TRAINING_SQL, {"since": since, "until": until})
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        for c in ("hg", "ag"):
            df[c] = df[c].astype(int)
    return df


def upcoming(conn: psycopg.Connection, hours: int = 48) -> pd.DataFrame:
    df = _frame(conn, UPCOMING_SQL, {"hours": hours})
    if not df.empty:
        df["kickoff"] = pd.to_datetime(df["kickoff"])
    return df


def display_odds(conn: psycopg.Connection, fixture_ids: list[int]) -> dict[int, dict]:
    """fixture_id -> {(market, selection, line): (price, captured_at, bookmaker)}."""
    out: dict[int, dict] = {}
    if not fixture_ids:
        return out
    for fid, m, s, line, price, at, book in conn.execute(DISPLAY_ODDS_SQL, {"ids": fixture_ids}).fetchall():
        out.setdefault(fid, {})[(m, s, None if line is None else float(line))] = (float(price), at, book)
    return out


def frozen_finished(conn: psycopg.Connection) -> pd.DataFrame:
    df = _frame(conn, FROZEN_SQL, {})
    if not df.empty:
        df["kickoff"] = pd.to_datetime(df["kickoff"])
    return df


def closing_1x2(conn: psycopg.Connection, fixture_ids: list[int]) -> pd.DataFrame:
    if not fixture_ids:
        return pd.DataFrame(columns=["fixture_id", "bookmaker", "price_h", "price_d", "price_a"])
    return _frame(conn, CLOSING_SQL, {"ids": fixture_ids})
