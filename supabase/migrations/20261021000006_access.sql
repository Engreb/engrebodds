-- Row-level security on every table. Writes go through the service role (ingestion and model jobs),
-- which bypasses RLS; anon and signed-in users can only read, and only what these policies allow.

-- Premium check. Stub until Phase 5 adds subscriptions.
create or replace function public.is_premium()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select false;
$$;

do $$
declare
  t text;
begin
  foreach t in array array[
    'leagues', 'league_seasons', 'teams', 'bookmakers', 'markets', 'ingestion_runs',
    'fixtures', 'fixture_team_stats', 'standings', 'odds_snapshots',
    'predictions', 'tips', 'tip_legs', 'slips', 'slip_legs'
  ] loop
    execute format('alter table public.%I enable row level security', t);
  end loop;

  -- Public reference and match data.
  foreach t in array array[
    'leagues', 'league_seasons', 'teams', 'bookmakers', 'markets',
    'fixtures', 'fixture_team_stats', 'standings', 'odds_snapshots'
  ] loop
    execute format('create policy "public read" on public.%I for select to anon, authenticated using (true)', t);
  end loop;
end;
$$;

-- Supabase grants anon/authenticated full table privileges by default and leaves RLS to filter rows,
-- so a blocked write silently touches 0 rows. Remove write privileges so writes fail loudly instead.
-- Phase 5 grants back only what members need (e.g. their own profile).
revoke insert, update, delete, truncate on all tables in schema public from anon, authenticated;
alter default privileges for role postgres in schema public
  revoke insert, update, delete, truncate on tables from anon, authenticated;

-- ingestion_runs: RLS on, no policies, so only the service role sees it.

-- Tips and slips: free ones always; every one once locked (so the whole track record is public);
-- the rest only for Premium members.
create policy "free, locked or premium" on public.tips
  for select to anon, authenticated
  using (is_free or locks_at <= now() or (select public.is_premium()));

create policy "visible with its tip" on public.tip_legs
  for select to anon, authenticated
  using (exists (select 1 from public.tips t where t.id = tip_id));

create policy "free, locked or premium" on public.slips
  for select to anon, authenticated
  using (is_free or locks_at <= now() or (select public.is_premium()));

create policy "visible with its slip" on public.slip_legs
  for select to anon, authenticated
  using (exists (select 1 from public.slips s where s.id = slip_id));

-- Full model probabilities: Premium, or once the fixture's tips have locked, or where the fixture has a free tip.
create policy "locked, free fixture or premium" on public.predictions
  for select to anon, authenticated
  using (
    (select public.is_premium())
    or exists (select 1 from public.fixtures f
                where f.id = fixture_id and f.kickoff_at - interval '1 hour' <= now())
    or exists (select 1 from public.tips t where t.fixture_id = predictions.fixture_id and t.is_free)
  );

-- Grading functions change data: service role only.
revoke execute on function public.settle_selection(bigint, text, text, numeric, timestamptz, timestamptz)
  from public, anon, authenticated;
revoke execute on function public.grade_tips(timestamptz) from public, anon, authenticated;
revoke execute on function public.grade_slips(timestamptz) from public, anon, authenticated;
revoke execute on function public.grade_all(timestamptz) from public, anon, authenticated;
grant execute on function public.settle_selection(bigint, text, text, numeric, timestamptz, timestamptz) to service_role;
grant execute on function public.grade_tips(timestamptz) to service_role;
grant execute on function public.grade_slips(timestamptz) to service_role;
grant execute on function public.grade_all(timestamptz) to service_role;
