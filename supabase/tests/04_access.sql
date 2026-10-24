-- Row-level security from an anonymous visitor's point of view.
-- Free tips: always visible. Premium tips, slips and predictions: only after full time (graded), or to members.
begin;
create extension if not exists pgtap with schema extensions;
select plan(15);

insert into public.teams (api_team_id, name) values (900001, 'Home FC'), (900002, 'Away FC');
insert into public.fixtures (api_fixture_id, league_id, season, kickoff_at, home_team_id, away_team_id)
select api, (select id from public.leagues where api_league_id = 399), 2027, now() + interval '3 hours',
       (select id from public.teams where api_team_id = 900001), (select id from public.teams where api_team_id = 900002)
  from (values (990001), (990002)) v(api);

-- Fixture 1: a free tip and a premium tip. Fixture 2: a premium tip that has locked (match about to start).
insert into public.tips (fixture_id, slot, model_prob, fair_odds, kickoff_at, is_free, confidence)
select f.id, 'result', 0.5, 1.9, f.kickoff_at, v.free, v.tag
  from public.fixtures f
  join (values (990001, true, 1), (990001, false, 2), (990002, false, 3)) v(api, free, tag) on v.api = f.api_fixture_id;
insert into public.tip_legs (tip_id, leg_no, market_code, selection) select id, 1, '1X2', '1' from public.tips;
update public.tips set locks_at = now() - interval '1 minute' where confidence = 3;
insert into public.slips (kind, slip_date, combined_odds, combined_prob, locks_at)
values ('odds2', current_date, 2.1, 0.45, now() + interval '2 hours');

insert into public.predictions (fixture_id, model_version, p_home, p_draw, p_away)
select id, 'test', 0.5, 0.3, 0.2 from public.fixtures where api_fixture_id in (990001, 990002);
insert into public.ingestion_runs (job) values ('test');
insert into public.tip_previews (run_id, window_start, kind, payload) values ('t', now(), 'tip', '{}'::jsonb);

set local role anon;

select is((select count(*) from public.leagues where is_pinned), 7::bigint, 'leagues are public');
select is((select string_agg(confidence::int::text, ',' order by confidence) from public.tips), '1',
          'before full time anon sees only the free tip (premium tips hidden, even after lock)');
select is((select count(*) from public.tip_legs), 1::bigint, 'legs follow their tip''s visibility');
select is((select count(*) from public.slips), 0::bigint, 'premium slips hidden before grading');
select is((select count(*) from public.predictions p join public.fixtures f on f.id = p.fixture_id
            where f.api_fixture_id in (990001, 990002)), 1::bigint,
          'predictions visible only for the fixture with a free tip until full time');
select is((select count(*) from public.ingestion_runs), 0::bigint, 'ingestion log is hidden');
select is((select count(*) from public.model_runs), 0::bigint, 'team ratings are hidden');
select is((select count(*) from public.tip_previews), 0::bigint, 'previews are hidden');
select is((select count(*) from public.track_record_summary), 0::bigint, 'summary view respects RLS (nothing graded)');

select throws_ok(
  $$ insert into public.tips (fixture_id, slot, model_prob, fair_odds, kickoff_at)
     select id, 'result', 0.5, 1.9, kickoff_at from public.fixtures limit 1 $$,
  '42501', null, 'anon cannot publish tips');
select throws_ok($$ update public.fixtures set status = 'FT' $$ , '42501', null,
                 'anon cannot change fixtures') ;
select throws_ok($$ select public.grade_all() $$, '42501', null, 'anon cannot run grading');

reset role;
set local role authenticated;
select is((select count(*) from public.tips), 1::bigint, 'signed-in non-member sees the same as anon');

-- Fixture 2 finishes and its premium tip is graded: now everyone can see it, and its prediction.
reset role;
update public.fixtures set status = 'FT', home_90 = 1, away_90 = 0 where api_fixture_id = 990002;
update public.tips set result = 'won', graded_at = now() where confidence = 3;
set local role anon;
select is((select string_agg(confidence::int::text, ',' order by confidence) from public.tips), '1,3',
          'after grading the premium tip is public');
select is((select count(*) from public.predictions p join public.fixtures f on f.id = p.fixture_id
            where f.api_fixture_id in (990001, 990002)), 2::bigint, 'and so is the finished fixture''s prediction');

reset role;
select * from finish();
rollback;
