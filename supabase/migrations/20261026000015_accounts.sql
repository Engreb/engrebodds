-- Phase 5: accounts and payments (docs/phase-5-plan.md). Test mode until a separate approval for live.
-- Payments and memberships are never deleted; deleting an account keeps them with a null user.
-- Every payment write goes through the service-role functions below (called by the Edge Functions).

-- ---------- settings: which payments count (test or live) ----------
create table public.app_settings (
  key         text primary key,
  value       text not null,
  updated_at  timestamptz not null default now()
);
insert into public.app_settings (key, value) values ('payments_mode', 'test');
alter table public.app_settings add constraint app_settings_payments_mode
  check (key <> 'payments_mode' or value in ('test', 'live'));
alter table public.app_settings enable row level security;   -- no policy: service role only
revoke all on public.app_settings from anon, authenticated;

create function public.payments_mode()
returns text
language sql
stable
security definer
set search_path = ''
as $$
  select value from public.app_settings where key = 'payments_mode';
$$;
revoke execute on function public.payments_mode() from public;
grant execute on function public.payments_mode() to anon, authenticated, service_role;

-- ---------- plans ----------
create table public.plans (
  id           text primary key,
  name         text not null,
  amount_kobo  integer not null check (amount_kobo > 0),
  days         integer not null check (days > 0),
  active       boolean not null default true,
  sort         integer not null default 0
);
insert into public.plans (id, name, amount_kobo, days, sort) values
  ('weekly', 'Weekly', 250000, 7, 1), ('monthly', 'Monthly', 750000, 30, 2), ('quarter', '3 months', 1800000, 90, 3);
alter table public.plans enable row level security;
create policy "public read" on public.plans for select to anon, authenticated using (true);
revoke all on public.plans from anon, authenticated;
grant select on public.plans to anon, authenticated;

-- ---------- profiles: the 18+ confirmation, nothing else ----------
create table public.profiles (
  user_id             uuid primary key references auth.users (id) on delete cascade,
  adult_confirmed_at  timestamptz not null,
  created_at          timestamptz not null default now()
);
alter table public.profiles enable row level security;
create policy "own profile" on public.profiles for select to authenticated using (user_id = (select auth.uid()));
create policy "create own profile" on public.profiles for insert to authenticated with check (user_id = (select auth.uid()));
revoke all on public.profiles from anon, authenticated;
grant select, insert on public.profiles to authenticated;

-- ---------- payments ----------
create table public.payments (
  id                    bigint generated always as identity primary key,
  reference             text not null unique,
  user_id               uuid references auth.users (id) on delete set null,
  plan_id               text not null references public.plans (id),
  mode                  text not null check (mode in ('test', 'live')),
  expected_amount_kobo  integer not null check (expected_amount_kobo > 0),
  expected_currency     text not null default 'NGN',
  amount_kobo           integer,
  currency              text,
  status                text not null default 'pending'
                        check (status in ('pending', 'success', 'failed', 'abandoned', 'needs_review')),
  paystack_id           bigint,
  channel               text,
  paid_at               timestamptz,
  raw                   jsonb,          -- accounting fields only (stripped by the Edge Functions)
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);
create index payments_user_idx on public.payments (user_id, created_at desc);
create index payments_pending_idx on public.payments (created_at) where status = 'pending';
alter table public.payments enable row level security;
create policy "own payments" on public.payments for select to authenticated using (user_id = (select auth.uid()));
revoke all on public.payments from anon, authenticated;
grant select on public.payments to authenticated;

create table public.payment_events (
  id           bigint generated always as identity primary key,
  event        text not null,
  reference    text,
  payment_id   bigint references public.payments (id),
  raw          jsonb,                   -- stripped like payments.raw
  received_at  timestamptz not null default now()
);
alter table public.payment_events enable row level security;   -- service role only
revoke all on public.payment_events from anon, authenticated;

-- ---------- memberships ----------
create table public.memberships (
  id             bigint generated always as identity primary key,
  user_id        uuid references auth.users (id) on delete set null,
  payment_id     bigint not null unique references public.payments (id),
  mode           text not null check (mode in ('test', 'live')),
  starts_at      timestamptz not null,
  ends_at        timestamptz not null check (ends_at > starts_at),
  revoked_at     timestamptz,
  revoke_reason  text,
  created_at     timestamptz not null default now(),
  check ((revoked_at is null) = (revoke_reason is null))
);
create index memberships_user_idx on public.memberships (user_id, mode, ends_at desc);
alter table public.memberships enable row level security;
-- Anon gets the grant too, so is_premium() can run with the caller's rights: auth.uid() is null, so no rows.
create policy "own memberships" on public.memberships for select to anon, authenticated using (user_id = (select auth.uid()));
revoke all on public.memberships from anon, authenticated;
grant select on public.memberships to anon, authenticated;

-- Only revocation (once) and the account-deletion null may change a membership.
create function public.memberships_guard()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if (new.payment_id, new.mode, new.starts_at, new.ends_at, new.created_at)
     is distinct from (old.payment_id, old.mode, old.starts_at, old.ends_at, old.created_at)
     or (new.user_id is distinct from old.user_id and new.user_id is not null)
     or (old.revoked_at is not null and (new.revoked_at, new.revoke_reason) is distinct from (old.revoked_at, old.revoke_reason)) then
    raise exception 'Membership % can only be revoked once, or detached from a deleted account', old.id
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;
create trigger memberships_guard before update on public.memberships for each row execute function public.memberships_guard();

-- ---------- alerts for Emmanuel ----------
create table public.admin_alerts (
  id           bigint generated always as identity primary key,
  kind         text not null,          -- amount_mismatch, deleted_user_payment, unknown_reference, refund, dispute, ...
  payment_id   bigint references public.payments (id),
  detail       jsonb not null default '{}'::jsonb,
  created_at   timestamptz not null default now(),
  emailed_at   timestamptz,            -- alert emails are required before PAYMENTS=live
  resolved_at  timestamptz
);
alter table public.admin_alerts enable row level security;   -- service role only
revoke all on public.admin_alerts from anon, authenticated;

-- No deletes, ever (track-record rule): payments, their events, memberships and alerts.
do $$
declare
  t text;
begin
  foreach t in array array['payments', 'payment_events', 'memberships', 'admin_alerts'] loop
    execute format('create trigger %I before delete on public.%I for each row execute function public.forbid_delete()',
                   t || '_no_delete', t);
    execute format('create trigger %I before truncate on public.%I for each statement execute function public.forbid_delete()',
                   t || '_no_truncate', t);
    execute format('revoke delete, truncate on public.%I from service_role', t);
  end loop;
end;
$$;

-- ---------- premium = a current, unrevoked membership in the current payments mode ----------
create or replace function public.is_premium()
returns boolean
language sql
stable
security invoker
set search_path = ''
as $$
  select exists (
    select 1 from public.memberships m
     where m.user_id = (select auth.uid())
       and m.mode = public.payments_mode()
       and m.revoked_at is null
       and now() >= m.starts_at and now() < m.ends_at
  );
$$;

-- ---------- service-role functions (Edge Functions) ----------

-- pay-start: checks, then a pending payment with the price it must be paid at.
create function public.start_payment(p_user uuid, p_plan text, p_reference text, p_site_mode text)
returns table (reference text, amount_kobo integer, currency text, mode text)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_plan public.plans;
  v_mode text := public.payments_mode();
begin
  if p_site_mode is distinct from v_mode then
    raise exception 'payments mode mismatch (site %, database %)', p_site_mode, v_mode using errcode = 'check_violation';
  end if;
  if not exists (select 1 from public.profiles where user_id = p_user) then
    raise exception 'confirm you are 18 or older first' using errcode = 'check_violation';
  end if;
  select * into v_plan from public.plans where id = p_plan and active;
  if not found then
    raise exception 'unknown or inactive plan %', p_plan using errcode = 'check_violation';
  end if;
  insert into public.payments (reference, user_id, plan_id, mode, expected_amount_kobo, expected_currency)
  values (p_reference, p_user, v_plan.id, v_mode, v_plan.amount_kobo, 'NGN');
  return query select p_reference, v_plan.amount_kobo, 'NGN'::text, v_mode;
end;
$$;

-- A verified successful charge. Only 'success' is terminal; a verified success also replaces pending,
-- abandoned or failed (e.g. paid after reconcile gave up). needs_review is never overwritten automatically.
create function public.record_payment(p_reference text, p_paystack_id bigint, p_amount integer, p_currency text,
                                      p_channel text, p_paid_at timestamptz, p_raw jsonb)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_pay public.payments;
  v_days integer;
  v_start timestamptz;
begin
  select * into v_pay from public.payments where reference = p_reference for update;
  if not found then
    insert into public.admin_alerts (kind, detail)
    values ('unknown_reference', jsonb_build_object('reference', p_reference, 'amount_kobo', p_amount, 'currency', p_currency));
    return 'unknown';
  end if;
  if v_pay.status in ('success', 'needs_review') then
    return v_pay.status;
  end if;

  update public.payments
     set amount_kobo = p_amount, currency = upper(p_currency), paystack_id = p_paystack_id, channel = p_channel,
         paid_at = p_paid_at, raw = p_raw, updated_at = now()
   where id = v_pay.id;

  if p_amount is distinct from v_pay.expected_amount_kobo or upper(p_currency) is distinct from v_pay.expected_currency then
    update public.payments set status = 'needs_review' where id = v_pay.id;
    insert into public.admin_alerts (kind, payment_id, detail)
    values ('amount_mismatch', v_pay.id, jsonb_build_object('expected_kobo', v_pay.expected_amount_kobo,
            'expected_currency', v_pay.expected_currency, 'paid_kobo', p_amount, 'paid_currency', p_currency));
    return 'needs_review';
  end if;
  if v_pay.user_id is null then
    update public.payments set status = 'needs_review' where id = v_pay.id;
    insert into public.admin_alerts (kind, payment_id, detail)
    values ('deleted_user_payment', v_pay.id, jsonb_build_object('reference', p_reference));
    return 'needs_review';
  end if;

  update public.payments set status = 'success' where id = v_pay.id;
  select days into v_days from public.plans where id = v_pay.plan_id;
  -- Stack after the latest unrevoked membership in the same mode. Revoking never moves later memberships.
  select greatest(now(), coalesce(max(ends_at), now())) into v_start
    from public.memberships
   where user_id = v_pay.user_id and mode = v_pay.mode and revoked_at is null;
  insert into public.memberships (user_id, payment_id, mode, starts_at, ends_at)
  values (v_pay.user_id, v_pay.id, v_pay.mode, v_start, v_start + make_interval(days => v_days));
  return 'success';
end;
$$;

create function public.revoke_membership(p_payment_id bigint, p_reason text)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  if coalesce(trim(p_reason), '') = '' then
    raise exception 'a reason is required' using errcode = 'check_violation';
  end if;
  update public.memberships set revoked_at = now(), revoke_reason = p_reason
   where payment_id = p_payment_id and revoked_at is null;
  if not found then
    raise exception 'no unrevoked membership for payment %', p_payment_id using errcode = 'no_data_found';
  end if;
end;
$$;

create function public.mark_abandoned(p_reference text)
returns boolean
language sql
security definer
set search_path = ''
as $$
  with u as (
    update public.payments set status = 'abandoned', updated_at = now()
     where reference = p_reference and status = 'pending'
    returning 1
  )
  select exists (select 1 from u);
$$;

-- delete-account: refused while a payment is still pending.
create function public.can_delete_account(p_user uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select not exists (select 1 from public.payments where user_id = p_user and status = 'pending');
$$;

do $$
declare
  f text;
begin
  foreach f in array array[
    'start_payment(uuid, text, text, text)',
    'record_payment(text, bigint, integer, text, text, timestamptz, jsonb)',
    'revoke_membership(bigint, text)',
    'mark_abandoned(text)',
    'can_delete_account(uuid)'
  ] loop
    execute format('revoke execute on function public.%s from public, anon, authenticated', f);
    execute format('grant execute on function public.%s to service_role', f);
  end loop;
end;
$$;
