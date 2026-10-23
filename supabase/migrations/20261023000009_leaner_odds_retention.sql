-- Leaner post-match odds retention (agreed 23 Oct 2026). Weekends bring ~1,300 allowlisted fixtures,
-- so keeping opening and closing prices for every selection would fill the free database in ~2 months.
-- The track-record price is stored on each tip (ref_odds), so after full time we keep only:
--   * the full price history of every tipped selection (all bookmakers), and
--   * closing prices (last before kick-off) for the main markets (1X2, DC, OU, BTTS) from every
--     stored bookmaker, for closing-line value and model calibration.
-- Everything else is deleted. Before kick-off nothing changes (movement arrows need the history).

create or replace function public.thin_odds(p_fixture_id bigint)
returns integer
language plpgsql
set search_path = ''
as $$
declare
  n integer;
begin
  with tipped as (
    select distinct l.market_code, l.selection, l.line
      from public.tips t
      join public.tip_legs l on l.tip_id = t.id
     where t.fixture_id = p_fixture_id
  ),
  keep as (
    (select o.id
       from public.odds_snapshots o
       join tipped s on s.market_code = o.market_code and s.selection = o.selection
                    and s.line is not distinct from o.line
      where o.fixture_id = p_fixture_id)
    union
    (select distinct on (o.bookmaker_id, o.market_code, o.selection, o.line) o.id
       from public.odds_snapshots o
       join public.fixtures f on f.id = o.fixture_id
      where o.fixture_id = p_fixture_id
        and o.market_code in ('1X2', 'DC', 'OU', 'BTTS')
        and o.captured_at < f.kickoff_at
      order by o.bookmaker_id, o.market_code, o.selection, o.line, o.captured_at desc)
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
