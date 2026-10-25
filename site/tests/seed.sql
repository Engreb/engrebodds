-- Local test data for the site (leak test and visual checks). Run against the LOCAL database only,
-- after `supabase db reset`. Everything sits in today's publishing window.
--
--   Fixture A (Lagos Lions v Abuja Stars):   free, 4 priced tips, visible to everyone.
--   Fixture B (Kano Kings v Enugu Eagles):   premium and pending, holds the banker. Its picks are the
--                                            canaries: "Under 3.5 goals", price 3.17, chance 61%.
--   Fixture C (Ibadan City v Benin Rovers):  premium, finished 2-0 and graded: visible to everyone.
--   Slips: odds2 (premium, locked: A + B legs) and odds5 (free: A + C legs).
begin;

-- The site shows today's publishing window (06:45 to 06:45 UTC): keep every match inside it. Tips need kick-off
-- at least an hour ahead, so between about 05:25 and 06:45 UTC this seed fails (and the leak test says so).
create function pg_temp.window_start() returns timestamptz language sql as
  $$ select date_trunc('day', now() - interval '6 hours 45 minutes') + interval '6 hours 45 minutes' $$;
create function pg_temp.window_end() returns timestamptz language sql as $$ select pg_temp.window_start() + interval '1 day' $$;

insert into public.leagues (api_league_id, name, country, type, tier, tips_enabled, display_order)
values (999001, 'Test Premier League', 'Nigeria', 'League', 'full', true, 1);

insert into public.teams (api_team_id, name) values
  (999101, 'Lagos Lions'), (999102, 'Abuja Stars'), (999103, 'Kano Kings'),
  (999104, 'Enugu Eagles'), (999105, 'Ibadan City'), (999106, 'Benin Rovers');

insert into public.fixtures (api_fixture_id, league_id, season, kickoff_at, home_team_id, away_team_id)
select v.api, (select id from public.leagues where api_league_id = 999001), 2026, v.ko,
       (select id from public.teams where api_team_id = v.h), (select id from public.teams where api_team_id = v.a)
  from (values (999201, 999101, 999102, least(now() + interval '3 hours', pg_temp.window_end() - interval '20 minutes')),
               (999202, 999103, 999104, least(now() + interval '4 hours', pg_temp.window_end() - interval '10 minutes')),
               (999203, 999105, 999106, now() + interval '2 hours')) v(api, h, a, ko);
create temp table seed_fx as
select v.tag, f.id from (values ('A', 999201), ('B', 999202), ('C', 999203)) v(tag, api)
  join public.fixtures f on f.api_fixture_id = v.api;

-- A past meeting and recent form, so match pages have facts.
insert into public.fixtures (api_fixture_id, league_id, season, kickoff_at, home_team_id, away_team_id, status, home_90, away_90)
select 999290 + n, (select id from public.leagues where api_league_id = 999001), 2026, now() - (n * interval '7 days'),
       (select id from public.teams where api_team_id = 999101 + (n % 2)), (select id from public.teams where api_team_id = 999102 - (n % 2)),
       'FT', n % 3, 1
  from generate_series(1, 4) n;

insert into public.predictions (fixture_id, model_version, lambda_home, lambda_away, rho, p_home, p_draw, p_away, probs, cs_top, data_quality, fresh_days)
select id, 'test', 1.7, 0.9, -0.05, 0.55, 0.25, 0.20,
       '{"1X2|1":0.55,"1X2|X":0.25,"1X2|2":0.20,"DC|1X":0.80,"DC|X2":0.45,"DC|12":0.75,"OU|O|1.5":0.74,"OU|U|1.5":0.26,
         "OU|O|2.5":0.50,"OU|U|2.5":0.50,"OU|O|3.5":0.28,"OU|U|3.5":0.72,"BTTS|Y":0.47,"BTTS|N":0.53,"CS|1-0":0.14}'::jsonb,
       '[{"score":"1-0","p":0.14},{"score":"2-0","p":0.12}]'::jsonb, 1.0, 3
  from seed_fx;
-- B's prediction carries the canary probability for its goals pick.
update public.predictions set probs = probs || '{"OU|U|3.5":0.6123,"1X2|2":0.4321,"1X2|1":0.3079}'::jsonb
 where fixture_id = (select id from seed_fx where tag = 'B');

create temp table seed_spec (tag text, slot text, market text, sel text, line numeric, p numeric, fair numeric,
                              price numeric, free boolean, banker boolean);
insert into seed_spec values
  ('A', 'result', '1X2', '1', null, 0.55, 1.71, 1.45, true, false),
  ('A', 'goals', 'OU', 'O', 1.5, 0.74, 1.27, 1.33, true, false),
  ('A', 'btts', 'BTTS', 'N', null, 0.53, 1.77, 1.80, true, false),
  ('B', 'result', '1X2', '2', null, 0.4321, 2.18, 2.30, false, true),
  ('B', 'goals', 'OU', 'U', 3.5, 0.6123, 1.54, 3.17, false, false),
  ('C', 'result', '1X2', '1', null, 0.55, 1.71, 1.62, false, false);
insert into public.tips (fixture_id, prediction_id, slot, is_banker, confidence, model_prob, fair_odds, ref_odds,
                         ref_bookmaker_id, ref_captured_at, p_source, is_free, kickoff_at)
select f.id, p.id, v.slot, v.banker, 5, v.p, v.fair, v.price, (select id from public.bookmakers where is_display),
       now() - interval '30 minutes', 'market', v.free, fx.kickoff_at
  from seed_spec v join seed_fx f on f.tag = v.tag join public.predictions p on p.fixture_id = f.id
  join public.fixtures fx on fx.id = f.id;
create temp table seed_tips as
select v.tag, v.slot, t.id from seed_spec v join seed_fx f on f.tag = v.tag
  join public.tips t on t.fixture_id = f.id and t.slot = v.slot;
insert into public.tip_legs (tip_id, leg_no, market_code, selection, line)
select t.id, 1, v.market, v.sel, v.line from seed_tips t join seed_spec v on v.tag = t.tag and v.slot = t.slot;

insert into public.slips (kind, slip_date, target_odds, combined_odds, combined_prob, ref_odds, ref_captured_at, is_free, locks_at)
select k, (now() - interval '6 hours 45 minutes')::date, tgt, odds, prob, odds, now() - interval '30 minutes', free, now() + interval '2 hours'
  from (values ('odds2', 2.0, 4.60, 0.3368, false), ('odds5', 5.0, 2.35, 0.3036, true)) v(k, tgt, odds, prob, free);
insert into public.slip_legs (slip_id, tip_id, odds)
select s.id, t.id, tp.ref_odds
  from public.slips s
  join seed_tips t on (s.kind = 'odds2' and ((t.tag = 'A' and t.slot = 'result') or (t.tag = 'B' and t.slot = 'goals')))
                   or (s.kind = 'odds5' and ((t.tag = 'A' and t.slot = 'result') or (t.tag = 'C' and t.slot = 'result')))
  join public.tips tp on tp.id = t.id
 where s.slip_date = (now() - interval '6 hours 45 minutes')::date;

-- C kicks off "now" and finishes 2-0; its tip is graded (won). Fixture times can move; the tip's lock has passed.
update public.fixtures set kickoff_at = greatest(now() - interval '2 hours', pg_temp.window_start() + interval '1 minute'), status = 'FT', home_90 = 2, away_90 = 0, home_ht = 1, away_ht = 0,
       finished_at = now() where id = (select id from seed_fx where tag = 'C');
update public.tips set kickoff_at = greatest(now() - interval '2 hours', pg_temp.window_start() + interval '1 minute'),
       locks_at = now() - interval '1 minute'
 where id in (select id from seed_tips where tag = 'C');
update public.tip_legs set result = 'won' where tip_id in (select id from seed_tips where tag = 'C');
update public.tips set result = 'won', graded_at = now() where id in (select id from seed_tips where tag = 'C');

commit;
