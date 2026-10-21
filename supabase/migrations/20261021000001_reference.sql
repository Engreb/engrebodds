-- Reference data: leagues, seasons, teams, bookmakers, ingestion log.
-- All times are UTC timestamptz; the site shows WAT.

create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

-- Leagues. tier decides what we publish:
--   full  = odds + match statistics: every market, including corners/cards/shots/fouls
--   goals = odds only: result, goals, GG/NG, double chance, correct score
--   off   = stored for reference, nothing published
-- Pinned leagues (NPFL, CAF) are always kept on, whatever the coverage flags say.
create table public.leagues (
  id             bigint generated always as identity primary key,
  api_league_id  integer not null unique,
  slug           text unique,
  name           text not null,
  country        text not null,
  country_code   text,
  type           text not null default 'League' check (type in ('League', 'Cup')),
  logo_url       text,
  tier           text not null default 'off' check (tier in ('full', 'goals', 'off')),
  is_pinned      boolean not null default false,
  display_order  integer,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);
create index leagues_tier_idx on public.leagues (tier) where tier <> 'off';

-- Season labels come from the API as-is: NPFL "2027" is 2026/27, Premier League "2024" is 2024/25.
create table public.league_seasons (
  league_id   bigint not null references public.leagues (id),
  season      integer not null,
  start_date  date,
  end_date    date,
  is_current  boolean not null default false,
  coverage    jsonb not null default '{}'::jsonb,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  primary key (league_id, season)
);

create table public.teams (
  id           bigint generated always as identity primary key,
  api_team_id  integer not null unique,
  name         text not null,
  short_name   text,
  country      text,
  logo_url     text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

-- store_odds: we snapshot this bookmaker's prices. is_display: the price shown on the site (exactly one).
create table public.bookmakers (
  id                bigint generated always as identity primary key,
  api_bookmaker_id  integer unique,
  name              text not null unique,
  is_nigerian       boolean not null default false,
  store_odds        boolean not null default false,
  is_display        boolean not null default false,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);
create unique index bookmakers_one_display_idx on public.bookmakers (is_display) where is_display;

create table public.ingestion_runs (
  id               bigint generated always as identity primary key,
  job              text not null,
  started_at       timestamptz not null default now(),
  finished_at      timestamptz,
  status           text not null default 'running' check (status in ('running', 'ok', 'failed')),
  requests_used    integer not null default 0,
  quota_remaining  integer,
  rows_written     integer not null default 0,
  notes            jsonb not null default '[]'::jsonb,   -- warnings, e.g. fixtures with inconsistent scores
  error            text
);
create index ingestion_runs_job_idx on public.ingestion_runs (job, started_at desc);

create trigger leagues_updated_at before update on public.leagues
  for each row execute function public.set_updated_at();
create trigger league_seasons_updated_at before update on public.league_seasons
  for each row execute function public.set_updated_at();
create trigger teams_updated_at before update on public.teams
  for each row execute function public.set_updated_at();
create trigger bookmakers_updated_at before update on public.bookmakers
  for each row execute function public.set_updated_at();

-- Bookmakers agreed after the coverage audit (21 Oct 2026): Bet365 displayed; 1xBet and Pinnacle also stored.
-- Nigerian bookmakers API-Football doesn't carry are listed for the bookmaker pages and later affiliate work.
insert into public.bookmakers (api_bookmaker_id, name, is_nigerian, store_odds, is_display) values
  (8,    'Bet365',   false, true,  true),
  (11,   '1xBet',    true,  true,  false),
  (4,    'Pinnacle', false, true,  false),
  (24,   'Betway',   true,  false, false),
  (null, 'SportyBet', true, false, false),
  (null, 'Bet9ja',    true, false, false),
  (null, 'BetKing',   true, false, false),
  (null, 'MSport',    true, false, false),
  (null, 'NairaBet',  true, false, false);

-- Pinned leagues (API-Football IDs from the audit). sync_reference fills in the rest and sets tier from coverage.
insert into public.leagues (api_league_id, name, country, type, is_pinned, tier) values
  (399,  'NPFL',                                  'Nigeria', 'League', true, 'goals'),
  (1231, 'Federation Cup',                        'Nigeria', 'Cup',    true, 'goals'),
  (12,   'CAF Champions League',                  'World',   'Cup',    true, 'full'),
  (20,   'CAF Confederation Cup',                 'World',   'Cup',    true, 'full'),
  (6,    'Africa Cup of Nations',                 'World',   'Cup',    true, 'full'),
  (36,   'Africa Cup of Nations - Qualification', 'World',   'Cup',    true, 'full'),
  (29,   'World Cup - Qualification Africa',      'World',   'Cup',    true, 'full');
