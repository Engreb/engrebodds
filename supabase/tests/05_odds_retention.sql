-- thin_odds keeps opening, closing and publication-time prices and drops the rest.
begin;
create extension if not exists pgtap with schema extensions;
select plan(6);

insert into public.teams (api_team_id, name) values (900001, 'Home FC'), (900002, 'Away FC');
insert into public.fixtures (api_fixture_id, league_id, season, kickoff_at, home_team_id, away_team_id)
select 990001, (select id from public.leagues where api_league_id = 399), 2027, now() + interval '10 hours',
       (select id from public.teams where api_team_id = 900001), (select id from public.teams where api_team_id = 900002);

create function pg_temp.fx() returns bigint language sql as $$
  select id from public.fixtures where api_fixture_id = 990001;
$$;
create function pg_temp.bet365() returns bigint language sql as $$
  select id from public.bookmakers where name = 'Bet365';
$$;

-- Price history for home win, one row per change: opening 2.10, then 2.00, 1.95 (current at publication),
-- 1.90, 1.85 (closing), and 1.70 captured after kick-off.
insert into public.odds_snapshots (fixture_id, bookmaker_id, market_code, selection, price, captured_at)
select pg_temp.fx(), pg_temp.bet365(), '1X2', '1', v.price, now() + v.at
  from (values (2.10, interval '0 hours'), (2.00, interval '1 hour'), (1.95, interval '2 hours'),
               (1.90, interval '4 hours'), (1.85, interval '8 hours'), (1.70, interval '11 hours')) v(price, at);
-- An away-win price that never changed: one row, which is both opening and closing.
insert into public.odds_snapshots (fixture_id, bookmaker_id, market_code, selection, price, captured_at)
values (pg_temp.fx(), pg_temp.bet365(), '1X2', '2', 3.40, now());

-- A tip published 3 hours from now (after the 1.95 price, before 1.90).
insert into public.tips (fixture_id, slot, model_prob, fair_odds, kickoff_at, published_at)
values (pg_temp.fx(), 'result', 0.5, 1.9, now() + interval '10 hours', now() + interval '3 hours');

select is((select odds_thinned_at from public.fixtures where id = pg_temp.fx()), null, 'not thinned yet');
select is(public.thin_odds(pg_temp.fx()), 3, 'three in-between prices removed');
select is((select string_agg(price::text, ',' order by captured_at) from public.odds_snapshots
            where fixture_id = pg_temp.fx() and selection = '1'),
          '2.10,1.95,1.85', 'kept opening, publication-time and closing prices');
select is((select count(*) from public.odds_snapshots where fixture_id = pg_temp.fx() and selection = '2'),
          1::bigint, 'an unchanged price keeps its single row');
select isnt((select odds_thinned_at from public.fixtures where id = pg_temp.fx()), null, 'fixture marked as thinned');
select is(public.thin_odds(pg_temp.fx()), 0, 'thinning again removes nothing');

select * from finish();
rollback;
