-- Phase 2 step 4: tip publishing behind the switch (docs/phase-2-step-4-plan.md).

-- ---------- tips: product, probability source, price snapshot time ----------
alter table public.tips
  add column product          text not null default 'likely' check (product in ('likely', 'value')),
  add column p_source         text check (p_source in ('market', 'model')),
  add column ref_captured_at  timestamptz;
alter table public.tips drop constraint tips_check;
alter table public.tips add constraint tips_ref_price_check
  check ((ref_odds is null) = (ref_bookmaker_id is null) and (ref_odds is null) = (ref_captured_at is null));

create or replace function public.tips_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    if new.locks_at is null then
      new.locks_at := new.kickoff_at - interval '1 hour';
    end if;
    if new.locks_at <= now() then
      raise exception 'Cannot publish a tip after its lock time (%)', new.locks_at
        using errcode = 'check_violation';
    end if;
  elsif old.locks_at <= now() and
        (new.fixture_id, new.prediction_id, new.slot, new.is_banker, new.confidence, new.model_prob, new.fair_odds,
         new.ref_odds, new.ref_bookmaker_id, new.ref_captured_at, new.product, new.p_source, new.is_free,
         new.kickoff_at, new.locks_at, new.published_at)
        is distinct from
        (old.fixture_id, old.prediction_id, old.slot, old.is_banker, old.confidence, old.model_prob, old.fair_odds,
         old.ref_odds, old.ref_bookmaker_id, old.ref_captured_at, old.product, old.p_source, old.is_free,
         old.kickoff_at, old.locks_at, old.published_at) then
    raise exception 'Tip % is locked; only its result can change', old.id
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

-- ---------- slips: the 1xBet accumulator price and its snapshot time ----------
alter table public.slips
  add column ref_odds         numeric(9, 2) check (ref_odds > 1),
  add column ref_captured_at  timestamptz,
  add constraint slips_ref_price_check check ((ref_odds is null) = (ref_captured_at is null));

create or replace function public.slips_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    if new.locks_at <= now() then
      raise exception 'Cannot publish a slip after its lock time (%)', new.locks_at
        using errcode = 'check_violation';
    end if;
  elsif old.locks_at <= now() and
        (new.kind, new.slip_date, new.target_odds, new.combined_odds, new.combined_prob, new.is_free,
         new.locks_at, new.published_at, new.ref_odds, new.ref_captured_at)
        is distinct from
        (old.kind, old.slip_date, old.target_odds, old.combined_odds, old.combined_prob, old.is_free,
         old.locks_at, old.published_at, old.ref_odds, old.ref_captured_at) then
    raise exception 'Slip % is locked; only its result can change', old.id
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

-- ---------- predictions: store rho, so tips can rebuild the score grid (builder combos) ----------
alter table public.predictions add column rho numeric(6, 4);

create or replace function public.predictions_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_kickoff timestamptz;
begin
  select kickoff_at into v_kickoff from public.fixtures where id = new.fixture_id;
  if v_kickoff - interval '1 hour' > now() then
    return new;
  end if;
  if tg_op = 'INSERT' then
    raise exception 'Cannot add a prediction inside the lock (kick-off %)', v_kickoff
      using errcode = 'check_violation';
  end if;
  if (new.fixture_id, new.model_version, new.lambda_home, new.lambda_away, new.rho, new.p_home, new.p_draw, new.p_away,
      new.cs_top, new.ht_score, new.data_quality, new.settings_id, new.fresh_days, new.computed_at)
     is distinct from
     (old.fixture_id, old.model_version, old.lambda_home, old.lambda_away, old.rho, old.p_home, old.p_draw, old.p_away,
      old.cs_top, old.ht_score, old.data_quality, old.settings_id, old.fresh_days, old.computed_at)
     or (new.probs <> '{}'::jsonb and new.probs is distinct from old.probs)
     or (new.final <> '{}'::jsonb and new.final is distinct from old.final) then
    raise exception 'Prediction for fixture % is frozen (kick-off %); only thinning is allowed', old.fixture_id, v_kickoff
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

-- ---------- leagues: which ones may get published tips (switched on by hand, after shadow evidence) ----------
alter table public.leagues
  add column tips_enabled     boolean not null default false,
  add column tips_enabled_at  timestamptz;

-- ---------- access: premium content only after full time ----------
-- Free tips are always visible. Premium tips and slips appear to non-members only once graded
-- (no longer at lock). Predictions and team ratings would reveal the premium tips, so they follow suit.
drop policy "free, locked or premium" on public.tips;
create policy "free, graded or premium" on public.tips
  for select to anon, authenticated
  using (is_free or result <> 'pending' or (select public.is_premium()));

drop policy "free, locked or premium" on public.slips;
create policy "free, graded or premium" on public.slips
  for select to anon, authenticated
  using (is_free or result <> 'pending' or (select public.is_premium()));

drop policy "locked, free fixture or premium" on public.predictions;
create policy "finished, free fixture or premium" on public.predictions
  for select to anon, authenticated
  using (
    (select public.is_premium())
    or exists (select 1 from public.fixtures f
                where f.id = fixture_id and f.status in ('FT', 'AET', 'PEN', 'CANC', 'ABD', 'AWD', 'WO'))
    or exists (select 1 from public.tips t where t.fixture_id = predictions.fixture_id and t.is_free)
  );

drop policy "public read" on public.model_runs;          -- team ratings: service role only

-- ---------- track record (published tips) ----------
-- Units, ROI and average odds use only tips with a market price (ref_odds); tips without one
-- ("no market price", e.g. builder) count in hit rate only. Voids are excluded from hit rate.
drop view public.track_record_summary;
drop view public.track_record;

create view public.track_record with (security_invoker = true) as
select t.id                                          as tip_id,
       t.product,
       t.kickoff_at,
       lg.name                                       as league,
       ht.name                                       as home,
       aw.name                                       as away,
       t.slot,
       t.is_banker,
       t.is_free,
       legs.description,
       t.model_prob,
       t.p_source,
       t.fair_odds,
       t.ref_odds,
       b.name                                        as ref_bookmaker,
       t.ref_captured_at,
       t.ref_odds                                    as odds,
       case when t.ref_odds is null then 'no market price' else 'bookmaker' end as odds_source,
       f.home_90,
       f.away_90,
       t.result,
       case when t.ref_odds is null then null
            when t.result = 'won' then t.ref_odds - 1
            when t.result = 'lost' then -1
            else 0 end                               as profit_units,
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

create view public.track_record_summary with (security_invoker = true) as
with d as (
  select 'all' as breakdown, 'All tips' as label, r.* from public.track_record r
  union all select 'product', r.product,   r.* from public.track_record r
  union all select 'slot',    r.slot,      r.* from public.track_record r
  union all select 'banker',  'Bankers',   r.* from public.track_record r where r.is_banker
  union all select 'free',    'Free tips', r.* from public.track_record r where r.is_free
  union all select 'league',  r.league,    r.* from public.track_record r
)
select breakdown,
       label,
       count(*) filter (where result in ('won', 'lost'))                                          as settled,
       count(*) filter (where result = 'won')                                                     as won,
       count(*) filter (where result = 'void')                                                    as void,
       round(count(*) filter (where result = 'won')::numeric
             / nullif(count(*) filter (where result in ('won', 'lost')), 0), 4)                   as hit_rate,
       count(*) filter (where result in ('won', 'lost') and odds is not null)                     as priced,
       count(*) filter (where result in ('won', 'lost') and odds is null)                         as no_market_price,
       round(avg(odds) filter (where result in ('won', 'lost')), 2)                               as avg_odds,
       round(sum(profit_units), 2)                                                                as units,
       round(sum(profit_units) filter (where result in ('won', 'lost'))
             / nullif(count(*) filter (where result in ('won', 'lost') and odds is not null), 0), 4) as roi
  from d
 group by breakdown, label;

create view public.slip_record with (security_invoker = true) as
select s.id as slip_id, s.kind, s.slip_date, s.combined_odds, s.combined_prob, s.ref_odds, s.ref_captured_at,
       s.is_free, s.result, s.settled_odds, s.graded_at,
       (select count(*) from public.slip_legs sl where sl.slip_id = s.id) as legs,
       case when s.ref_odds is null then null
            when s.result = 'won' then coalesce(s.settled_odds, s.ref_odds) - 1
            when s.result = 'lost' then -1
            else 0 end as profit_units
  from public.slips s
 where s.result <> 'pending';

create view public.slip_record_summary with (security_invoker = true) as
select kind,
       count(*) filter (where result in ('won', 'lost'))                        as settled,
       count(*) filter (where result = 'won')                                   as won,
       count(*) filter (where result = 'void')                                  as void,
       round(count(*) filter (where result = 'won')::numeric
             / nullif(count(*) filter (where result in ('won', 'lost')), 0), 4) as hit_rate,
       round(avg(ref_odds) filter (where result in ('won', 'lost')), 2)         as avg_price,
       round(avg(combined_prob) filter (where result in ('won', 'lost')), 4)    as avg_chance_all_land,
       round(sum(profit_units), 2)                                              as units,
       round(sum(profit_units) filter (where result in ('won', 'lost'))
             / nullif(count(*) filter (where result in ('won', 'lost') and ref_odds is not null), 0), 4) as roi
  from public.slip_record
 group by kind;

-- ---------- dry-run previews (not the track record; replaced on every preview run) ----------
create table public.tip_previews (
  id            bigint generated always as identity primary key,
  run_id        text not null,
  created_at    timestamptz not null default now(),
  window_start  timestamptz not null,
  kind          text not null check (kind in ('tip', 'slip')),
  fixture_id    bigint references public.fixtures (id),
  payload       jsonb not null
);
alter table public.tip_previews enable row level security;       -- no policy: service role only
revoke insert, update, delete, truncate on public.tip_previews from anon, authenticated;
