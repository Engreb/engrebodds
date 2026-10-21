-- Predictions, tips and slips. Phase 2 (the model) fills these; Phase 1 defines them and their rules:
--   * nothing is ever deleted (no DELETE, no TRUNCATE, even for the service role);
--   * a tip or slip cannot be added once locked (1 hour before kick-off),
--     and after that only its grading fields can change.

create table public.predictions (
  id             bigint generated always as identity primary key,
  fixture_id     bigint not null references public.fixtures (id),
  model_version  text not null,
  lambda_home    numeric(5, 3),
  lambda_away    numeric(5, 3),
  p_home         numeric(5, 4),
  p_draw         numeric(5, 4),
  p_away         numeric(5, 4),
  probs          jsonb not null default '{}'::jsonb,   -- keyed 'MARKET|selection|line', e.g. 'OU|O|2.5'
  cs_top         jsonb,                                 -- most likely scores with probabilities
  ht_score       text,
  data_quality   numeric(4, 3),
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  unique (fixture_id, model_version)
);

-- One row per published tip. Combo tips ("1X & O1.5") have several legs in tip_legs.
-- model_prob is the chance the whole tip lands; ref_odds is the displayed bookmaker's price at publication.
create table public.tips (
  id                bigint generated always as identity primary key,
  fixture_id        bigint not null references public.fixtures (id),
  prediction_id     bigint references public.predictions (id),
  slot              text not null
                    check (slot in ('result', 'goals', 'btts', 'builder', 'market', 'stats')),
  is_banker         boolean not null default false,   -- a flag, not a slot, so bankers aren't counted twice
  confidence        numeric(3, 1) check (confidence between 0 and 10),
  model_prob        numeric(5, 4) not null check (model_prob > 0 and model_prob <= 1),
  fair_odds         numeric(7, 2) not null check (fair_odds > 1),
  ref_odds          numeric(7, 2) check (ref_odds > 1),
  ref_bookmaker_id  bigint references public.bookmakers (id),
  is_free           boolean not null default false,
  kickoff_at        timestamptz not null,   -- kick-off when published; postponement rules use this
  locks_at          timestamptz not null,
  published_at      timestamptz not null default now(),
  result            text not null default 'pending' check (result in ('pending', 'won', 'lost', 'void')),
  graded_at         timestamptz,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  check ((ref_odds is null) = (ref_bookmaker_id is null))
);
create index tips_fixture_idx on public.tips (fixture_id);
create index tips_pending_idx on public.tips (locks_at) where result = 'pending';
create index tips_kickoff_idx on public.tips (kickoff_at desc);

create table public.tip_legs (
  id           bigint generated always as identity primary key,
  tip_id       bigint not null references public.tips (id),
  leg_no       smallint not null check (leg_no >= 1),
  market_code  text not null references public.markets (code),
  selection    text not null,
  line         numeric(4, 1) check (line is null or line * 2 = trunc(line * 2)),
  result       text not null default 'pending' check (result in ('pending', 'won', 'lost', 'void')),
  unique (tip_id, leg_no)
);

create table public.slips (
  id             bigint generated always as identity primary key,
  kind           text not null check (kind in ('odds2', 'odds5', 'odds10', 'low_risk', 'bankers')),
  slip_date      date not null,
  target_odds    numeric(7, 2),
  combined_odds  numeric(9, 2) not null check (combined_odds > 1),
  combined_prob  numeric(6, 5) not null check (combined_prob > 0 and combined_prob <= 1),  -- chance every leg lands
  is_free        boolean not null default false,
  locks_at       timestamptz not null,    -- earliest leg's lock
  published_at   timestamptz not null default now(),
  result         text not null default 'pending' check (result in ('pending', 'won', 'lost', 'void')),
  settled_odds   numeric(9, 2),           -- combined odds after void legs drop out
  graded_at      timestamptz,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  unique (kind, slip_date)
);

create table public.slip_legs (
  slip_id  bigint not null references public.slips (id),
  tip_id   bigint not null references public.tips (id),
  odds     numeric(7, 2) not null check (odds > 1),
  primary key (slip_id, tip_id)
);
create index slip_legs_tip_idx on public.slip_legs (tip_id);

create trigger predictions_updated_at before update on public.predictions
  for each row execute function public.set_updated_at();
create trigger tips_updated_at before update on public.tips
  for each row execute function public.set_updated_at();
create trigger slips_updated_at before update on public.slips
  for each row execute function public.set_updated_at();

-- ---------- selection validation ----------

create or replace function public.valid_selection(p_rule text, p_selection text)
returns boolean
language sql
immutable
set search_path = ''
as $$
  select case p_rule
    when '1x2'         then p_selection in ('1', 'X', '2')
    when 'dc'          then p_selection in ('1X', 'X2', '12')
    when 'ou'          then p_selection in ('O', 'U')
    when 'team_ou'     then p_selection in ('HO', 'HU', 'AO', 'AU')
    when 'btts'        then p_selection in ('Y', 'N')
    when 'clean_sheet' then p_selection in ('H', 'A')
    when 'cs'          then p_selection ~ '^[0-9]{1,2}-[0-9]{1,2}$'
    else false
  end;
$$;

-- ---------- integrity triggers ----------

create or replace function public.forbid_delete()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'Rows in % are never deleted (track-record rule)', tg_table_name
    using errcode = 'insufficient_privilege';
end;
$$;

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
         new.ref_odds, new.ref_bookmaker_id, new.is_free, new.kickoff_at, new.locks_at, new.published_at)
        is distinct from
        (old.fixture_id, old.prediction_id, old.slot, old.is_banker, old.confidence, old.model_prob, old.fair_odds,
         old.ref_odds, old.ref_bookmaker_id, old.is_free, old.kickoff_at, old.locks_at, old.published_at) then
    raise exception 'Tip % is locked; only its result can change', old.id
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create or replace function public.tip_legs_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_locks  timestamptz;
  v_rule   text;
  v_line   boolean;
begin
  select locks_at into v_locks from public.tips where id = new.tip_id;
  select rule, needs_line into v_rule, v_line from public.markets where code = new.market_code;

  if not public.valid_selection(v_rule, new.selection) then
    raise exception 'Selection % is not valid for market %', new.selection, new.market_code
      using errcode = 'check_violation';
  end if;
  if v_line <> (new.line is not null) then
    raise exception 'Market % %', new.market_code, case when v_line then 'needs a line' else 'takes no line' end
      using errcode = 'check_violation';
  end if;

  if v_locks <= now() then
    if tg_op = 'INSERT' then
      raise exception 'Cannot add a leg to tip % after its lock time', new.tip_id
        using errcode = 'check_violation';
    elsif (new.tip_id, new.leg_no, new.market_code, new.selection, new.line)
          is distinct from (old.tip_id, old.leg_no, old.market_code, old.selection, old.line) then
      raise exception 'Leg % of tip % is locked; only its result can change', old.leg_no, old.tip_id
        using errcode = 'check_violation';
    end if;
  end if;
  return new;
end;
$$;

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
         new.locks_at, new.published_at)
        is distinct from
        (old.kind, old.slip_date, old.target_odds, old.combined_odds, old.combined_prob, old.is_free,
         old.locks_at, old.published_at) then
    raise exception 'Slip % is locked; only its result can change', old.id
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create or replace function public.slip_legs_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if (select locks_at from public.slips where id = coalesce(new.slip_id, old.slip_id)) <= now() then
    raise exception 'Slip % is locked; its legs cannot change', coalesce(new.slip_id, old.slip_id)
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create trigger tips_guard before insert or update on public.tips
  for each row execute function public.tips_guard();
create trigger tip_legs_guard before insert or update on public.tip_legs
  for each row execute function public.tip_legs_guard();
create trigger slips_guard before insert or update on public.slips
  for each row execute function public.slips_guard();
create trigger slip_legs_guard before insert or update on public.slip_legs
  for each row execute function public.slip_legs_guard();

-- No deletes, ever: triggers cover every role (including the owner); revokes are a second layer.
do $$
declare
  t text;
begin
  foreach t in array array['tips', 'tip_legs', 'slips', 'slip_legs'] loop
    execute format('create trigger %I before delete on public.%I for each row execute function public.forbid_delete()',
                   t || '_no_delete', t);
    execute format('create trigger %I before truncate on public.%I for each statement execute function public.forbid_delete()',
                   t || '_no_truncate', t);
    execute format('revoke delete, truncate on public.%I from anon, authenticated, service_role', t);
  end loop;
end;
$$;
