-- Automatic grading and the public track record.
--
-- Settlement rules (settle_selection):
--   * FT, AET, PEN: settled on the 90-minute score (half-time score for 1H markets,
--     full-match statistics for corners/cards/shots/fouls).
--   * CANC, ABD, AWD, WO: void.
--   * Not played (PST, SUSP, INT, TBD, still NS ...): pending, then void 48h after the published kick-off.
--   * Played, but moved more than 48h from the published kick-off: void.
--   * Played, but the score is flagged as inconsistent (score_issue): pending until checked by hand.
--   * Played, but the numbers are missing (e.g. statistics not fetched): pending, then void after 72h.
--   * Over/under landing exactly on a whole line: void (push).
-- Tips: lost if any leg lost; otherwise pending if any leg pending; otherwise void if any leg void
--   (bet-builder convention); otherwise won. Every tip is graded on its own.
-- Slips: lost if any tip lost; pending if any pending; void if all void; otherwise won,
--   with settled_odds recalculated without the void legs.

create or replace function public.settle_selection(
  p_fixture_id   bigint,
  p_market       text,
  p_selection    text,
  p_line         numeric,
  p_tip_kickoff  timestamptz,
  p_now          timestamptz default now()
)
returns text
language plpgsql
stable
set search_path = ''
as $$
declare
  f    public.fixtures%rowtype;
  m    public.markets%rowtype;
  h    integer;
  a    integer;
  tot  integer;
  won  boolean;
begin
  select * into f from public.fixtures where id = p_fixture_id;
  select * into m from public.markets where code = p_market;
  if f.id is null or m.code is null then
    raise exception 'Unknown fixture % or market %', p_fixture_id, p_market;
  end if;

  if f.status in ('CANC', 'ABD', 'AWD', 'WO') then
    return 'void';
  end if;

  if f.status not in ('FT', 'AET', 'PEN') then
    return case when p_now > p_tip_kickoff + interval '48 hours' then 'void' else 'pending' end;
  end if;

  if f.kickoff_at > p_tip_kickoff + interval '48 hours' then
    return 'void';
  end if;

  if f.score_issue is not null then
    return 'pending';
  end if;

  if m.stat = 'goals' then
    if m.period = '1H' then
      h := f.home_ht; a := f.away_ht;
    else
      h := f.home_90; a := f.away_90;
    end if;
  else
    -- API-Football reports a zero red-card count as null, so red cards default to 0.
    select max(v) filter (where team_id = f.home_team_id),
           max(v) filter (where team_id = f.away_team_id)
      into h, a
    from (
      select s.team_id,
             case m.stat
               when 'corners'         then s.corners
               when 'cards'           then s.yellow_cards + coalesce(s.red_cards, 0)
               when 'shots'           then s.shots_total
               when 'shots_on_target' then s.shots_on_target
               when 'fouls'           then s.fouls
             end as v
      from public.fixture_team_stats s
      where s.fixture_id = f.id
    ) x;
  end if;

  if h is null or a is null then
    return case when p_now > coalesce(f.finished_at, f.kickoff_at) + interval '72 hours' then 'void' else 'pending' end;
  end if;

  if m.rule in ('ou', 'team_ou') then
    tot := case
             when m.rule = 'ou' then h + a
             when left(p_selection, 1) = 'H' then h
             else a
           end;
    if tot = p_line then
      return 'void';
    end if;
    won := (right(p_selection, 1) = 'O') = (tot > p_line);
  else
    won := case m.rule
      when '1x2' then case p_selection when '1' then h > a when 'X' then h = a else h < a end
      when 'dc' then case p_selection when '1X' then h >= a when 'X2' then h <= a else h <> a end
      when 'btts' then (h > 0 and a > 0) = (p_selection = 'Y')
      when 'clean_sheet' then case p_selection when 'H' then a = 0 else h = 0 end
      when 'cs' then p_selection = h || '-' || a
    end;
  end if;

  return case when won then 'won' else 'lost' end;
end;
$$;

create or replace function public.grade_tips(p_now timestamptz default now())
returns integer
language plpgsql
set search_path = ''
as $$
declare
  n integer;
begin
  update public.tip_legs l
     set result = public.settle_selection(t.fixture_id, l.market_code, l.selection, l.line, t.kickoff_at, p_now)
    from public.tips t
   where t.id = l.tip_id
     and t.result = 'pending'
     and t.kickoff_at <= p_now;

  with agg as (
    select l.tip_id,
           case when bool_or(l.result = 'lost')    then 'lost'
                when bool_or(l.result = 'pending') then 'pending'
                when bool_or(l.result = 'void')    then 'void'
                else 'won' end as res
      from public.tip_legs l
      join public.tips t on t.id = l.tip_id
     where t.result = 'pending'
       and t.kickoff_at <= p_now
     group by l.tip_id
  )
  update public.tips t
     set result = agg.res, graded_at = p_now
    from agg
   where agg.tip_id = t.id
     and agg.res <> 'pending';

  get diagnostics n = row_count;
  return n;
end;
$$;

create or replace function public.grade_slips(p_now timestamptz default now())
returns integer
language plpgsql
set search_path = ''
as $$
declare
  n integer;
begin
  with agg as (
    select sl.slip_id,
           case when bool_or(t.result = 'lost')    then 'lost'
                when bool_or(t.result = 'pending') then 'pending'
                when bool_and(t.result = 'void')   then 'void'
                else 'won' end as res,
           exp(sum(ln(sl.odds)) filter (where t.result = 'won')) as won_odds
      from public.slip_legs sl
      join public.tips t on t.id = sl.tip_id
      join public.slips s on s.id = sl.slip_id
     where s.result = 'pending'
     group by sl.slip_id
  )
  update public.slips s
     set result = agg.res,
         settled_odds = case when agg.res = 'won' then round(agg.won_odds::numeric, 2) end,
         graded_at = p_now
    from agg
   where agg.slip_id = s.id
     and agg.res <> 'pending';

  get diagnostics n = row_count;
  return n;
end;
$$;

-- Called by sync_results / sync_stats after every update.
create or replace function public.grade_all(p_now timestamptz default now())
returns jsonb
language plpgsql
set search_path = ''
as $$
declare
  n_tips   integer;
  n_slips  integer;
begin
  n_tips  := public.grade_tips(p_now);
  n_slips := public.grade_slips(p_now);
  return jsonb_build_object('tips', n_tips, 'slips', n_slips);
end;
$$;

-- ---------- track record ----------

-- One row per graded tip. Profit is at a 1-unit stake, using the displayed bookmaker's price at publication;
-- where there was none (e.g. a league without odds), the model's fair price, flagged in odds_source.
create view public.track_record with (security_invoker = true) as
select t.id                                          as tip_id,
       t.kickoff_at,
       lg.name                                       as league,
       ht.name                                       as home,
       aw.name                                       as away,
       t.slot,
       t.is_banker,
       t.is_free,
       legs.description,
       t.model_prob,
       t.fair_odds,
       t.ref_odds,
       b.name                                        as ref_bookmaker,
       coalesce(t.ref_odds, t.fair_odds)             as odds,
       case when t.ref_odds is null then 'fair' else 'bookmaker' end as odds_source,
       f.home_90,
       f.away_90,
       t.result,
       case t.result
         when 'won'  then coalesce(t.ref_odds, t.fair_odds) - 1
         when 'lost' then -1
         else 0
       end                                           as profit_units,
       t.graded_at
  from public.tips t
  join public.fixtures f  on f.id = t.fixture_id
  join public.leagues lg  on lg.id = f.league_id
  join public.teams ht    on ht.id = f.home_team_id
  join public.teams aw    on aw.id = f.away_team_id
  left join public.bookmakers b on b.id = t.ref_bookmaker_id
  cross join lateral (
    select string_agg(l.market_code || ' ' || l.selection || coalesce(' ' || l.line, ''), ' & ' order by l.leg_no)
             as description
      from public.tip_legs l
     where l.tip_id = t.id
  ) legs
 where t.result <> 'pending';

-- Hit rate always comes with average odds, units and ROI. Void tips are excluded from all of them.
create view public.track_record_summary with (security_invoker = true) as
with d as (
  select 'all' as breakdown, 'All tips' as label, r.* from public.track_record r
  union all select 'slot',   r.slot,      r.* from public.track_record r
  union all select 'banker', 'Bankers',   r.* from public.track_record r where r.is_banker
  union all select 'free',   'Free tips', r.* from public.track_record r where r.is_free
  union all select 'league', r.league,    r.* from public.track_record r
)
select breakdown,
       label,
       count(*) filter (where result in ('won', 'lost'))                        as settled,
       count(*) filter (where result = 'won')                                   as won,
       count(*) filter (where result = 'void')                                  as void,
       round(count(*) filter (where result = 'won')::numeric
             / nullif(count(*) filter (where result in ('won', 'lost')), 0), 4) as hit_rate,
       round(avg(odds) filter (where result in ('won', 'lost')), 2)             as avg_odds,
       round(sum(profit_units), 2)                                              as units,
       round(sum(profit_units)
             / nullif(count(*) filter (where result in ('won', 'lost')), 0), 4) as roi,
       count(*) filter (where result in ('won', 'lost') and odds_source = 'fair') as fair_priced
  from d
 group by breakdown, label;
