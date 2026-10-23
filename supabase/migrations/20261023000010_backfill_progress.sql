-- Backfill progress: when a past league-season's fixtures and results were loaded in full.
-- The backfill job works through league-seasons where this is null, one request each.
alter table public.league_seasons add column fixtures_backfilled_at timestamptz;

create index league_seasons_backfill_idx on public.league_seasons (season)
  where fixtures_backfilled_at is null;
