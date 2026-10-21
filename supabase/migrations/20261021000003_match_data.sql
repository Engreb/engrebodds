-- Fixtures, per-team match statistics, standings and odds snapshots.

-- Bets settle on the 90-minute score (home_90/away_90). Extra time and penalties are kept separately.
create table public.fixtures (
  id                bigint generated always as identity primary key,
  api_fixture_id    integer not null unique,
  league_id         bigint not null references public.leagues (id),
  season            integer not null,
  round             text,
  kickoff_at        timestamptz not null,
  home_team_id      bigint not null references public.teams (id),
  away_team_id      bigint not null references public.teams (id),
  status            text not null default 'NS',   -- API short code: NS, 1H, HT, 2H, FT, AET, PEN, PST, CANC, ABD, AWD, WO ...
  elapsed           smallint,
  home_ht           smallint,
  away_ht           smallint,
  home_90           smallint,
  away_90           smallint,
  home_et           smallint,
  away_et           smallint,
  home_pen          smallint,
  away_pen          smallint,
  finished_at       timestamptz,
  stats_fetched_at  timestamptz,
  odds_thinned_at   timestamptz,
  -- A score that doesn't add up (e.g. "PEN" with no penalty score). Tips on the fixture stay pending
  -- until someone checks it, corrects the score and sets manual_score so the sync stops overwriting it.
  score_issue       text,
  manual_score      boolean not null default false,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  check (home_team_id <> away_team_id)
);
create index fixtures_kickoff_idx on public.fixtures (kickoff_at);
create index fixtures_league_kickoff_idx on public.fixtures (league_id, kickoff_at);
create index fixtures_home_idx on public.fixtures (home_team_id, kickoff_at desc);
create index fixtures_away_idx on public.fixtures (away_team_id, kickoff_at desc);
create index fixtures_unfinished_idx on public.fixtures (kickoff_at)
  where status not in ('FT', 'AET', 'PEN', 'CANC', 'ABD', 'AWD', 'WO');

create table public.fixture_team_stats (
  fixture_id       bigint not null references public.fixtures (id),
  team_id          bigint not null references public.teams (id),
  corners          smallint,
  yellow_cards     smallint,
  red_cards        smallint,
  shots_total      smallint,
  shots_on_target  smallint,
  fouls            smallint,
  possession       numeric(4, 1),
  xg               numeric(4, 2),
  raw              jsonb,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  primary key (fixture_id, team_id)
);

create table public.standings (
  league_id      bigint not null references public.leagues (id),
  season         integer not null,
  group_name     text not null default '',
  team_id        bigint not null references public.teams (id),
  rank           smallint not null,
  played         smallint not null default 0,
  won            smallint not null default 0,
  drawn          smallint not null default 0,
  lost           smallint not null default 0,
  goals_for      smallint not null default 0,
  goals_against  smallint not null default 0,
  points         smallint not null default 0,
  form           text,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  primary key (league_id, season, group_name, team_id)
);

-- Append-only price history. Movement arrows and closing prices are derived from it.
create table public.odds_snapshots (
  id            bigint generated always as identity primary key,
  fixture_id    bigint not null references public.fixtures (id),
  bookmaker_id  bigint not null references public.bookmakers (id),
  market_code   text not null references public.markets (code),
  selection     text not null,
  line          numeric(4, 1),
  price         numeric(7, 2) not null check (price > 1),
  captured_at   timestamptz not null default now(),
  check (line is null or line * 2 = trunc(line * 2))
);
create index odds_snapshots_lookup_idx
  on public.odds_snapshots (fixture_id, bookmaker_id, market_code, selection, line, captured_at desc);

create trigger fixtures_updated_at before update on public.fixtures
  for each row execute function public.set_updated_at();
create trigger fixture_team_stats_updated_at before update on public.fixture_team_stats
  for each row execute function public.set_updated_at();
create trigger standings_updated_at before update on public.standings
  for each row execute function public.set_updated_at();

-- Latest price per selection, with movement against the previous snapshot:
-- 'up' = price drifted (longer), 'down' = price shortened, null = no earlier price or unchanged.
create view public.odds_latest with (security_invoker = true) as
select fixture_id, bookmaker_id, market_code, selection, line, price, captured_at,
       case when prev_price is null or prev_price = price then null
            when price > prev_price then 'up' else 'down' end as movement
from (
  select o.*,
         lead(o.price) over w as prev_price,
         row_number() over w as rn
  from public.odds_snapshots o
  window w as (partition by o.fixture_id, o.bookmaker_id, o.market_code, o.selection, o.line
               order by o.captured_at desc)
) s
where rn = 1;

-- Last price captured before kick-off (closing line), for measuring closing-line value later.
create view public.odds_closing with (security_invoker = true) as
select distinct on (o.fixture_id, o.bookmaker_id, o.market_code, o.selection, o.line)
       o.fixture_id, o.bookmaker_id, o.market_code, o.selection, o.line, o.price, o.captured_at
from public.odds_snapshots o
join public.fixtures f on f.id = o.fixture_id
where o.captured_at < f.kickoff_at
order by o.fixture_id, o.bookmaker_id, o.market_code, o.selection, o.line, o.captured_at desc;
