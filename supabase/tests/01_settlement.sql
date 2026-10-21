-- settle_selection: every market rule and every status path.
begin;
create extension if not exists pgtap with schema extensions;
select plan(26);

insert into public.teams (api_team_id, name) values (900001, 'Home FC'), (900002, 'Away FC');

-- 1-1 after 90 minutes, 2-1 after extra time, 0-1 at half-time.
insert into public.fixtures (api_fixture_id, league_id, season, kickoff_at, home_team_id, away_team_id,
                             status, home_ht, away_ht, home_90, away_90, home_et, away_et, finished_at)
select 990001, (select id from public.leagues where api_league_id = 399), 2027, now() + interval '2 hours',
       (select id from public.teams where api_team_id = 900001), (select id from public.teams where api_team_id = 900002),
       'AET', 0, 1, 1, 1, 2, 1, now() + interval '4 hours';

-- s(market, selection, line, hours after kick-off at which we grade, published kick-off offset in hours)
create function pg_temp.s(p_market text, p_sel text, p_line numeric default null,
                          p_after_h integer default 24, p_tip_offset_h integer default 0)
returns text language sql as $$
  select public.settle_selection(f.id, p_market, p_sel, p_line,
                                 f.kickoff_at + make_interval(hours => p_tip_offset_h),
                                 f.kickoff_at + make_interval(hours => p_after_h))
    from public.fixtures f where f.api_fixture_id = 990001;
$$;

-- Goals markets settle on 90 minutes, not extra time.
select is(pg_temp.s('1X2', 'X'), 'won', '1X2 draw settles on the 90-minute score');
select is(pg_temp.s('1X2', '1'), 'lost', '1X2 home loses although home won after extra time');
select is(pg_temp.s('DC', '1X'), 'won', 'double chance 1X');
select is(pg_temp.s('DC', '12'), 'lost', 'double chance 12');
select is(pg_temp.s('OU', 'O', 1.5), 'won', 'over 1.5');
select is(pg_temp.s('OU', 'U', 2.5), 'won', 'under 2.5');
select is(pg_temp.s('OU', 'O', 2.0), 'void', 'total exactly on a whole line is void');
select is(pg_temp.s('TEAM_OU', 'HO', 0.5), 'won', 'home to score');
select is(pg_temp.s('TEAM_OU', 'AU', 0.5), 'lost', 'away under 0.5');
select is(pg_temp.s('BTTS', 'Y'), 'won', 'GG');
select is(pg_temp.s('BTTS', 'N'), 'lost', 'NG');
select is(pg_temp.s('CLEAN_SHEET', 'H'), 'lost', 'home clean sheet');
select is(pg_temp.s('CS', '1-1'), 'won', 'correct score uses 90 minutes');
select is(pg_temp.s('CS', '2-1'), 'lost', 'extra-time score is not the correct score');
select is(pg_temp.s('HT_1X2', '2'), 'won', 'half-time result');
select is(pg_temp.s('HT_CS', '0-1'), 'won', 'half-time correct score');

-- Statistics markets.
select is(pg_temp.s('CORNERS_OU', 'O', 9.5), 'pending', 'no statistics yet: pending');
select is(pg_temp.s('CORNERS_OU', 'O', 9.5, 24 * 4), 'void', 'statistics still missing after 72h: void');

insert into public.fixture_team_stats (fixture_id, team_id, corners, yellow_cards, red_cards, shots_total, fouls)
select f.id, f.home_team_id, 6, 2, null, 12, 10 from public.fixtures f where f.api_fixture_id = 990001
union all
select f.id, f.away_team_id, 4, 1, 1, 9, 12 from public.fixtures f where f.api_fixture_id = 990001;

select is(pg_temp.s('CORNERS_OU', 'O', 9.5), 'won', 'corners 6 + 4 over 9.5');
select is(pg_temp.s('CARDS_OU', 'O', 3.5), 'won', 'cards 2 + 0 (null red) + 1 + 1 over 3.5');
select is(pg_temp.s('FOULS_OU', 'U', 20.5), 'lost', 'fouls 10 + 12 under 20.5');

-- Status paths.
update public.fixtures set status = 'CANC' where api_fixture_id = 990001;
select is(pg_temp.s('1X2', '1'), 'void', 'cancelled: void');

update public.fixtures set status = 'PST' where api_fixture_id = 990001;
select is(pg_temp.s('1X2', '1', null, 24), 'pending', 'postponed, within 48h: pending');
select is(pg_temp.s('1X2', '1', null, 72), 'void', 'postponed, past 48h: void');

update public.fixtures set status = 'FT' where api_fixture_id = 990001;
select is(pg_temp.s('1X2', 'X', null, 24, -72), 'void', 'played more than 48h after the published kick-off: void');

update public.fixtures set status = '2H' where api_fixture_id = 990001;
select is(pg_temp.s('1X2', 'X', null, 1), 'pending', 'in play: pending');

select * from finish();
rollback;
