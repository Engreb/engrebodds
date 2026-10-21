-- Track-record integrity: no deletes, no late tips, locked tips can only be graded.
begin;
create extension if not exists pgtap with schema extensions;
select plan(13);

insert into public.teams (api_team_id, name) values (900001, 'Home FC'), (900002, 'Away FC');
insert into public.fixtures (api_fixture_id, league_id, season, kickoff_at, home_team_id, away_team_id)
select 990001, (select id from public.leagues where api_league_id = 399), 2027, now() + interval '3 hours',
       (select id from public.teams where api_team_id = 900001), (select id from public.teams where api_team_id = 900002);

create function pg_temp.fx() returns bigint language sql as $$
  select id from public.fixtures where api_fixture_id = 990001;
$$;

insert into public.tips (fixture_id, slot, model_prob, fair_odds, kickoff_at)
values (pg_temp.fx(), 'result', 0.5, 1.9, now() + interval '3 hours');

select is((select locks_at from public.tips limit 1), (select kickoff_at - interval '1 hour' from public.tips limit 1),
          'locks_at defaults to one hour before kick-off');

select throws_ok(
  $$ insert into public.tips (fixture_id, slot, model_prob, fair_odds, kickoff_at)
     values (pg_temp.fx(), 'result', 0.5, 1.9, now() + interval '30 minutes') $$,
  '23514', null, 'cannot publish a tip inside the final hour');

select throws_ok(
  $$ insert into public.tip_legs (tip_id, leg_no, market_code, selection)
     values ((select id from public.tips limit 1), 1, '1X2', 'H') $$,
  '23514', null, 'invalid selection rejected');

select throws_ok(
  $$ insert into public.tip_legs (tip_id, leg_no, market_code, selection)
     values ((select id from public.tips limit 1), 1, 'OU', 'O') $$,
  '23514', null, 'over/under without a line rejected');

select lives_ok(
  $$ insert into public.tip_legs (tip_id, leg_no, market_code, selection)
     values ((select id from public.tips limit 1), 1, '1X2', '1') $$,
  'valid leg accepted before lock');

select throws_ok($$ delete from public.tips $$, '42501', null, 'tips cannot be deleted');
select throws_ok($$ delete from public.tip_legs $$, '42501', null, 'tip legs cannot be deleted');
select throws_ok($$ truncate public.tips cascade $$, '42501', null, 'tips cannot be truncated');
select ok(not has_table_privilege('service_role', 'public.tips', 'DELETE'), 'service role has no DELETE on tips');

-- Lock the tip (allowed: it is not locked yet), then try to edit it.
update public.tips set locks_at = now() - interval '1 minute';

select throws_ok($$ update public.tips set fair_odds = 2.5 $$, '23514', null, 'locked tip odds cannot change');
select throws_ok($$ update public.tip_legs set selection = '2' $$, '23514', null, 'locked leg selection cannot change');
select throws_ok(
  $$ insert into public.tip_legs (tip_id, leg_no, market_code, selection)
     values ((select id from public.tips limit 1), 2, 'BTTS', 'Y') $$,
  '23514', null, 'no legs added after lock');
select lives_ok($$ update public.tips set result = 'won', graded_at = now() $$, 'locked tip can still be graded');

select * from finish();
rollback;
