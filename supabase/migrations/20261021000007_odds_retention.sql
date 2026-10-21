-- Lean odds retention (agreed 22 Oct 2026). Snapshots are only written when a price changes.
-- Once a fixture is over, its history is thinned to the prices that matter:
--   * the opening price of every selection,
--   * the closing price (last before kick-off), for closing-line value,
--   * the prices current when each tip on the fixture was published.
-- Odds history is not part of the track record, so deleting from odds_snapshots is allowed.

create or replace function public.thin_odds(p_fixture_id bigint)
returns integer
language plpgsql
set search_path = ''
as $$
declare
  n integer;
begin
  with keep as (
    (select distinct on (o.bookmaker_id, o.market_code, o.selection, o.line) o.id
       from public.odds_snapshots o
      where o.fixture_id = p_fixture_id
      order by o.bookmaker_id, o.market_code, o.selection, o.line, o.captured_at)
    union
    (select distinct on (o.bookmaker_id, o.market_code, o.selection, o.line) o.id
       from public.odds_snapshots o
       join public.fixtures f on f.id = o.fixture_id
      where o.fixture_id = p_fixture_id
        and o.captured_at < f.kickoff_at
      order by o.bookmaker_id, o.market_code, o.selection, o.line, o.captured_at desc)
    union
    (select distinct on (t.id, o.bookmaker_id, o.market_code, o.selection, o.line) o.id
       from public.tips t
       join public.odds_snapshots o on o.fixture_id = t.fixture_id and o.captured_at <= t.published_at
      where t.fixture_id = p_fixture_id
      order by t.id, o.bookmaker_id, o.market_code, o.selection, o.line, o.captured_at desc)
  )
  delete from public.odds_snapshots o
   where o.fixture_id = p_fixture_id
     and o.id not in (select id from keep);
  get diagnostics n = row_count;

  update public.fixtures set odds_thinned_at = now() where id = p_fixture_id;
  return n;
end;
$$;

revoke execute on function public.thin_odds(bigint) from public, anon, authenticated;
grant execute on function public.thin_odds(bigint) to service_role;
