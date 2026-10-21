-- grade_all: single and combo tips, slips with void legs, and the track-record summary.
begin;
create extension if not exists pgtap with schema extensions;
select plan(17);

insert into public.teams (api_team_id, name) values (900001, 'Home FC'), (900002, 'Away FC');
insert into public.fixtures (api_fixture_id, league_id, season, kickoff_at, home_team_id, away_team_id)
select api, (select id from public.leagues where api_league_id = 399), 2027, now() + interval '2 hours',
       (select id from public.teams where api_team_id = 900001), (select id from public.teams where api_team_id = 900002)
  from (values (990001), (990002)) v(api);

create temp table ids (name text primary key, id bigint);

-- add_tip(name, fixture api id, slot, legs [{m, s, l}], model prob, fair odds, displayed odds, free, banker)
create function pg_temp.add_tip(p_name text, p_fixture integer, p_slot text, p_legs jsonb,
                                p_prob numeric, p_fair numeric, p_ref numeric default null,
                                p_free boolean default false, p_banker boolean default false)
returns bigint language plpgsql as $$
declare
  v_fix  public.fixtures%rowtype;
  v_id   bigint;
  v_leg  jsonb;
  v_no   integer := 0;
begin
  select * into v_fix from public.fixtures where api_fixture_id = p_fixture;
  insert into public.tips (fixture_id, slot, is_banker, model_prob, fair_odds, ref_odds, ref_bookmaker_id, is_free, kickoff_at)
  values (v_fix.id, p_slot, p_banker, p_prob, p_fair, p_ref,
          case when p_ref is not null then (select id from public.bookmakers where is_display) end,
          p_free, v_fix.kickoff_at)
  returning id into v_id;
  for v_leg in select * from jsonb_array_elements(p_legs) loop
    v_no := v_no + 1;
    insert into public.tip_legs (tip_id, leg_no, market_code, selection, line)
    values (v_id, v_no, v_leg->>'m', v_leg->>'s', (v_leg->>'l')::numeric);
  end loop;
  insert into ids values (p_name, v_id);
  return v_id;
end;
$$;

create function pg_temp.res(p_name text) returns text language sql as $$
  select t.result from public.tips t join ids on ids.id = t.id where ids.name = p_name;
$$;

select pg_temp.add_tip('home',    990001, 'result',  '[{"m":"1X2","s":"1"}]',                        0.55, 1.75, 2.00, true, true);
select pg_temp.add_tip('combo',   990001, 'builder', '[{"m":"DC","s":"1X"},{"m":"OU","s":"O","l":1.5}]', 0.52, 1.80);
select pg_temp.add_tip('combo_x', 990001, 'builder', '[{"m":"1X2","s":"2"},{"m":"OU","s":"O","l":1.5}]', 0.15, 6.00, 7.50);
select pg_temp.add_tip('corners', 990001, 'stats',   '[{"m":"CORNERS_OU","s":"O","l":9.5}]',          0.50, 1.85, 1.90);
select pg_temp.add_tip('gg',      990002, 'btts',    '[{"m":"BTTS","s":"Y"}]',                        0.58, 1.65, 1.70);

insert into public.slips (kind, slip_date, target_odds, combined_odds, combined_prob, locks_at)
values ('odds2', current_date, 2, 3.40, 0.32, now() + interval '1 hour');
insert into public.slip_legs (slip_id, tip_id, odds)
select s.id, ids.id, v.odds
  from public.slips s, ids join (values ('home', 2.00), ('gg', 1.70)) v(name, odds) on v.name = ids.name
 where s.kind = 'odds2';

-- Fixture 1 ends 2-0 (1-0 at half-time); fixture 2 is postponed.
update public.fixtures set status = 'FT', home_ht = 1, away_ht = 0, home_90 = 2, away_90 = 0,
                           finished_at = kickoff_at + interval '2 hours'
 where api_fixture_id = 990001;
update public.fixtures set status = 'PST' where api_fixture_id = 990002;

select is(public.grade_all(now() + interval '5 hours'), '{"tips": 3, "slips": 0}'::jsonb,
          'first pass grades the three settled tips and no slips');
select is(pg_temp.res('home'), 'won', 'single tip won');
select is(pg_temp.res('combo'), 'won', 'combo won when every leg wins');
select is(pg_temp.res('combo_x'), 'lost', 'combo lost when one leg loses');
select is(pg_temp.res('corners'), 'pending', 'stats tip waits for statistics');
select is(pg_temp.res('gg'), 'pending', 'postponed tip waits');
select is((select result from public.slips where kind = 'odds2'), 'pending', 'slip waits for its postponed leg');
select is((select string_agg(result, ',' order by leg_no) from public.tip_legs l join ids on ids.id = l.tip_id
            where ids.name = 'combo_x'), 'lost,won', 'each combo leg keeps its own result');

-- Four days later: statistics never arrived and the postponed match was not played.
select is(public.grade_all(now() + interval '4 days'), '{"tips": 2, "slips": 1}'::jsonb, 'second pass');
select is(pg_temp.res('corners'), 'void', 'stats tip void after 72h without statistics');
select is(pg_temp.res('gg'), 'void', 'postponed tip void after 48h');
select is((select result || ' ' || settled_odds from public.slips where kind = 'odds2'), 'won 2.00',
          'slip won with the void leg removed from its odds');

-- Track record: 3 settled (home won 2.00, combo won 1.80 at fair odds, combo_x lost), 2 void.
select is((select row(settled, won, void, hit_rate, avg_odds, units, roi, fair_priced)::text
             from public.track_record_summary where breakdown = 'all'),
          row(3, 2, 2, 0.6667, 3.77, 0.80, 0.2667, 1)::text,
          'summary: hit rate with average odds, units and ROI; voids excluded');
select is((select units from public.track_record_summary where breakdown = 'banker'), 1.00, 'bankers counted once');
select is((select settled from public.track_record_summary where breakdown = 'free'), 1::bigint, 'free tips');
select is((select odds_source from public.track_record r join ids on ids.id = r.tip_id where ids.name = 'combo'),
          'fair', 'a tip without bookmaker odds is flagged as fair-priced');
select is((select count(*) from public.track_record), 5::bigint, 'every graded tip, voids included, is on the record');

select * from finish();
rollback;
