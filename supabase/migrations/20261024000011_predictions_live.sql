-- Phase 2 step 3: daily predictions, model settings, shadow evaluation (docs/phase-2-step-3-plan.md).

-- ---------- model settings ----------
-- One active row. tune_live writes new rows inactive (with its validation ladder); activation is manual.
create table public.model_settings (
  id            bigint generated always as identity primary key,
  version       text not null unique,
  is_active     boolean not null default false,
  source        text not null,
  config        jsonb not null,
  calibrator    jsonb not null default '{"maps": {}, "draw_shift": 0}'::jsonb,
  validated_on  text[],
  ladder        jsonb,
  created_at    timestamptz not null default now(),
  activated_at  timestamptz
);
create unique index model_settings_one_active_idx on public.model_settings (is_active) where is_active;

-- One row per league and model version, replaced on every run (so it doesn't grow).
create table public.model_runs (
  league_id      bigint not null references public.leagues (id),
  model_version  text not null,
  settings_id    bigint references public.model_settings (id),
  fitted_at      timestamptz not null default now(),
  train_from     timestamptz,
  train_to       timestamptz,
  n_matches      integer not null,
  params         jsonb not null,
  primary key (league_id, model_version)
);

-- ---------- predictions ----------
-- probs holds every market's p_model. final holds the market-based p_final for 1X2 and O/U 2.5 only,
-- as {key: {p, bookmaker, captured_at}}; any key not in final has p_final = p_model (p_source 'model').
alter table public.predictions
  add column settings_id  bigint references public.model_settings (id),
  add column fresh_days   integer,
  add column computed_at  timestamptz not null default now(),
  add column final        jsonb not null default '{}'::jsonb;

create index predictions_fixture_idx on public.predictions (fixture_id);

-- Predictions freeze at kick-off minus 1 hour (the same lock as tips). After that, the only change
-- allowed is thinning (clearing probs and final). No new predictions inside the lock either.
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
  if (new.fixture_id, new.model_version, new.lambda_home, new.lambda_away, new.p_home, new.p_draw, new.p_away,
      new.cs_top, new.ht_score, new.data_quality, new.settings_id, new.fresh_days, new.computed_at)
     is distinct from
     (old.fixture_id, old.model_version, old.lambda_home, old.lambda_away, old.p_home, old.p_draw, old.p_away,
      old.cs_top, old.ht_score, old.data_quality, old.settings_id, old.fresh_days, old.computed_at)
     or (new.probs <> '{}'::jsonb and new.probs is distinct from old.probs)
     or (new.final <> '{}'::jsonb and new.final is distinct from old.final) then
    raise exception 'Prediction for fixture % is frozen (kick-off %); only thinning is allowed', old.fixture_id, v_kickoff
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create trigger predictions_guard before insert or update on public.predictions
  for each row execute function public.predictions_guard();

-- One row per market: p_model, p_final, where p_final came from, and the market snapshot behind it.
create view public.prediction_markets with (security_invoker = true) as
select p.fixture_id,
       p.model_version,
       p.settings_id,
       e.key                                                  as market_key,
       split_part(e.key, '|', 1)                              as market_code,
       split_part(e.key, '|', 2)                              as selection,
       nullif(split_part(e.key, '|', 3), '')::numeric         as line,
       e.value::numeric                                       as p_model,
       coalesce((p.final -> e.key ->> 'p')::numeric, e.value::numeric) as p_final,
       case when p.final ? e.key then 'market' else 'model' end as p_source,
       p.final -> e.key ->> 'bookmaker'                       as bookmaker,
       (p.final -> e.key ->> 'captured_at')::timestamptz      as captured_at
  from public.predictions p
 cross join lateral jsonb_each_text(p.probs) e;

-- Drop the per-market detail of old predictions without tips; headline columns (lambda, 1X2) stay.
create or replace function public.thin_predictions(p_days integer default 30)
returns integer
language plpgsql
set search_path = ''
as $$
declare
  n integer;
begin
  update public.predictions p
     set probs = '{}'::jsonb, final = '{}'::jsonb
    from public.fixtures f
   where f.id = p.fixture_id
     and f.kickoff_at < now() - make_interval(days => p_days)
     and (p.probs <> '{}'::jsonb or p.final <> '{}'::jsonb)
     and not exists (select 1 from public.tips t where t.fixture_id = p.fixture_id);
  get diagnostics n = row_count;
  return n;
end;
$$;

-- ---------- shadow evaluation ----------
create table public.shadow_reports (
  id             bigint generated always as identity primary key,
  week_start     date not null,
  settings_id    bigint references public.model_settings (id),
  model_version  text,
  created_at     timestamptz not null default now(),
  results        jsonb not null
);

-- ---------- access ----------
alter table public.model_settings enable row level security;
alter table public.model_runs enable row level security;
alter table public.shadow_reports enable row level security;     -- no policy: service role only
create policy "public read" on public.model_settings for select to anon, authenticated using (true);
create policy "public read" on public.model_runs for select to anon, authenticated using (true);
revoke insert, update, delete, truncate on public.model_settings, public.model_runs, public.shadow_reports
  from anon, authenticated;
revoke execute on function public.thin_predictions(integer) from public, anon, authenticated;
grant execute on function public.thin_predictions(integer) to service_role;

-- ---------- displayed bookmaker: 1xBet (operates in Nigeria) ----------
-- sync_odds reads is_display: 1xBet gets every market from the next odds run, Bet365 drops to main markets.
update public.bookmakers set is_display = false where name = 'Bet365';
update public.bookmakers set is_display = true where name = '1xBet';
