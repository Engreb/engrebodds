-- What the website can learn about locked content: that it exists, never what it is.
begin;
create extension if not exists pgtap with schema extensions;
select plan(9);

insert into public.teams (api_team_id, name) values (900101, 'Site Home'), (900102, 'Site Away');
insert into public.fixtures (api_fixture_id, league_id, season, kickoff_at, home_team_id, away_team_id)
select api, (select id from public.leagues where api_league_id = 399), 2027, now() + interval '3 hours',
       (select id from public.teams where api_team_id = 900101), (select id from public.teams where api_team_id = 900102)
  from (values (990101), (990102)) v(api);

-- Fixture 1: one free tip. Fixture 2: two premium tips, one the banker.
insert into public.tips (fixture_id, slot, model_prob, fair_odds, kickoff_at, is_free, is_banker)
select f.id, v.slot, 0.6, 1.57, f.kickoff_at, v.free, v.banker
  from public.fixtures f
  join (values (990101, 'result', true, false), (990102, 'result', false, true), (990102, 'goals', false, false))
       v(api, slot, free, banker) on v.api = f.api_fixture_id;
insert into public.predictions (fixture_id, model_version, p_home, p_draw, p_away)
select id, 'test', 0.5, 0.3, 0.2 from public.fixtures where api_fixture_id = 990101;
insert into public.slips (kind, slip_date, combined_odds, combined_prob, locks_at)
values ('odds5', current_date, 5.12, 0.21, now() + interval '2 hours');
insert into public.slip_legs (slip_id, tip_id, odds)
select (select id from public.slips where kind = 'odds5' and slip_date = current_date), t.id, 2.26
  from public.tips t join public.fixtures f on f.id = t.fixture_id where f.api_fixture_id = 990102;

set local role anon;

select is((select count(*) from public.tips t join public.fixtures f on f.id = t.fixture_id
            where f.api_fixture_id = 990102), 0::bigint, 'premium tips themselves stay hidden');
select is((select tips from public.fixture_tip_status(now(), now() + interval '1 day') s
             join public.fixtures f on f.id = s.fixture_id where f.api_fixture_id = 990102), 2,
          'but the site can tell the match has 2 premium tips');
select is((select has_banker from public.fixture_tip_status(now(), now() + interval '1 day') s
             join public.fixtures f on f.id = s.fixture_id where f.api_fixture_id = 990102), true,
          'and which match holds the banker');
select is((select has_free::text || predicted::text from public.fixture_tip_status(now(), now() + interval '1 day') s
             join public.fixtures f on f.id = s.fixture_id where f.api_fixture_id = 990101), 'truetrue',
          'free flag, and the covered (predicted) flag');
select is((select count(*) from public.fixture_tip_status(now(), now() + interval '30 days')), 0::bigint,
          'refuses ranges longer than 8 days');
select is((select count(*) from pg_proc p, unnest(p.proargnames) n
            where p.proname = 'fixture_tip_status'
              and n in ('selection', 'market_code', 'model_prob', 'fair_odds', 'ref_odds', 'confidence', 'slot')),
          0::bigint, 'tip status exposes no selection, probability or price');
select results_eq($$ select kind, legs, combined_odds from public.slip_status(current_date) $$,
                  $$ values ('odds5'::text, 2, 5.12::numeric) $$, 'locked slip: kind, leg count and odds only');
select is((select count(*) from public.slips), 0::bigint, 'the slip row itself stays hidden');
select throws_ok($$ select payload from public.proof_runs $$, '42501', null, 'proof payloads (premium tips before reveal) are hidden');

select * from finish();
rollback;
