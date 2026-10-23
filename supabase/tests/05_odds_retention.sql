-- thin_odds after full time keeps the full history of tipped selections and main-market closing prices,
-- and deletes the rest.
begin;
create extension if not exists pgtap with schema extensions;
select plan(8);

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
create function pg_temp.rows(p_market text, p_sel text) returns bigint language sql as $$
  select count(*) from public.odds_snapshots where fixture_id = pg_temp.fx() and market_code = p_market and selection = p_sel;
$$;

-- Home win (will be tipped): six price changes, the last captured after kick-off.
insert into public.odds_snapshots (fixture_id, bookmaker_id, market_code, selection, price, captured_at)
select pg_temp.fx(), pg_temp.bet365(), '1X2', '1', v.price, now() + v.at
  from (values (2.10, interval '0 hours'), (2.00, interval '1 hour'), (1.95, interval '2 hours'),
               (1.90, interval '4 hours'), (1.85, interval '8 hours'), (1.70, interval '11 hours')) v(price, at);
-- Away win (main market, not tipped): one unchanged price.
insert into public.odds_snapshots (fixture_id, bookmaker_id, market_code, selection, price, captured_at)
values (pg_temp.fx(), pg_temp.bet365(), '1X2', '2', 3.40, now());
-- Over 2.5 (main market, not tipped): three changes; only the closing 1.80 should stay.
insert into public.odds_snapshots (fixture_id, bookmaker_id, market_code, selection, line, price, captured_at)
select pg_temp.fx(), pg_temp.bet365(), 'OU', 'O', 2.5, v.price, now() + v.at
  from (values (1.95, interval '0 hours'), (1.88, interval '3 hours'), (1.80, interval '9 hours')) v(price, at);
-- Correct score 1-0 (not a main market, not tipped): all of it goes.
insert into public.odds_snapshots (fixture_id, bookmaker_id, market_code, selection, price, captured_at)
select pg_temp.fx(), pg_temp.bet365(), 'CS', '1-0', v.price, now() + v.at
  from (values (8.50, interval '0 hours'), (8.00, interval '5 hours')) v(price, at);

-- A home-win tip published 3 hours from now.
insert into public.tips (fixture_id, slot, model_prob, fair_odds, kickoff_at, published_at)
values (pg_temp.fx(), 'result', 0.5, 1.9, now() + interval '10 hours', now() + interval '3 hours');
insert into public.tip_legs (tip_id, leg_no, market_code, selection)
select id, 1, '1X2', '1' from public.tips where fixture_id = pg_temp.fx();

select is((select odds_thinned_at from public.fixtures where id = pg_temp.fx()), null, 'not thinned yet');
select is(public.thin_odds(pg_temp.fx()), 4, 'four rows removed: two over-2.5 changes and both correct-score prices');
select is(pg_temp.rows('1X2', '1'), 6::bigint, 'tipped selection keeps its full history');
select is(pg_temp.rows('1X2', '2'), 1::bigint, 'untipped main-market selection keeps its closing price');
select is((select string_agg(price::text, ',') from public.odds_snapshots
            where fixture_id = pg_temp.fx() and market_code = 'OU'), '1.80', 'only the closing over-2.5 price stays');
select is(pg_temp.rows('CS', '1-0'), 0::bigint, 'untipped non-main market is deleted');
select isnt((select odds_thinned_at from public.fixtures where id = pg_temp.fx()), null, 'fixture marked as thinned');
select is(public.thin_odds(pg_temp.fx()), 0, 'thinning again removes nothing');

select * from finish();
rollback;
