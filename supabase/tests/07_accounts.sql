-- Accounts and payments (Phase 5): who is premium, how payments settle, and what never changes.
begin;
create extension if not exists pgtap with schema extensions;
select plan(37);

-- Two users: a member-to-be and a signed-in non-member.
insert into auth.users (id, email, aud, role) values
  ('11111111-1111-1111-1111-111111111111', 'member@test.local', 'authenticated', 'authenticated'),
  ('22222222-2222-2222-2222-222222222222', 'other@test.local', 'authenticated', 'authenticated');

create function pg_temp.as_user(u uuid) returns void language sql as $$
  select set_config('request.jwt.claims', json_build_object('sub', u, 'role', 'authenticated')::text, true);
$$;

-- A pending premium tip, to see the policies follow is_premium().
insert into public.teams (api_team_id, name) values (900301, 'Acc Home'), (900302, 'Acc Away');
insert into public.fixtures (api_fixture_id, league_id, season, kickoff_at, home_team_id, away_team_id)
select 990301, (select id from public.leagues where api_league_id = 399), 2027, now() + interval '3 hours',
       (select id from public.teams where api_team_id = 900301), (select id from public.teams where api_team_id = 900302);
insert into public.tips (fixture_id, slot, model_prob, fair_odds, kickoff_at, is_free)
select id, 'result', 0.6, 1.57, kickoff_at, false from public.fixtures where api_fixture_id = 990301;

-- ---------- starting a payment ----------
select throws_ok($$ select * from public.start_payment('11111111-1111-1111-1111-111111111111', 'monthly', 'ref-1', 'test') $$,
                 '23514', 'confirm you are 18 or older first', 'no payment before the 18+ confirmation');

set local role authenticated;
select pg_temp.as_user('11111111-1111-1111-1111-111111111111');
insert into public.profiles (user_id, adult_confirmed_at) values ('11111111-1111-1111-1111-111111111111', now());
select throws_ok($$ insert into public.profiles (user_id, adult_confirmed_at) values ('22222222-2222-2222-2222-222222222222', now()) $$,
                 '42501', null, 'a user cannot create someone else''s profile');
select throws_ok($$ select * from public.start_payment('11111111-1111-1111-1111-111111111111', 'monthly', 'ref-x', 'test') $$,
                 '42501', null, 'users cannot call the payment functions');
reset role;

select throws_ok($$ select * from public.start_payment('11111111-1111-1111-1111-111111111111', 'monthly', 'ref-1', 'live') $$,
                 '23514', null, 'site and database payment modes must match');
select throws_ok($$ select * from public.start_payment('11111111-1111-1111-1111-111111111111', 'nope', 'ref-1', 'test') $$,
                 '23514', null, 'unknown plans are refused');
select results_eq($$ select amount_kobo, currency, mode from public.start_payment('11111111-1111-1111-1111-111111111111', 'monthly', 'ref-1', 'test') $$,
                  $$ values (750000, 'NGN'::text, 'test'::text) $$, 'pending payment carries the expected price and mode');

-- ---------- not premium yet ----------
set local role authenticated;
select pg_temp.as_user('11111111-1111-1111-1111-111111111111');
select is(public.is_premium(), false, 'a pending payment is not premium');
select is((select count(*) from public.tips t join public.fixtures f on f.id = t.fixture_id where f.api_fixture_id = 990301),
          0::bigint, 'pending premium tip hidden before paying');
select is((select status from public.payments where reference = 'ref-1'), 'pending', 'users can read their own payments');
reset role;

-- ---------- a wrong amount goes to review ----------
select public.start_payment('11111111-1111-1111-1111-111111111111', 'weekly', 'ref-short', 'test');
select is(public.record_payment('ref-short', 1001, 100000, 'NGN', 'card', now(), '{}'), 'needs_review', 'underpayment goes to review');
select is((select count(*) from public.admin_alerts a join public.payments p on p.id = a.payment_id
            where p.reference = 'ref-short' and a.kind = 'amount_mismatch'), 1::bigint, 'and raises an alert');
select is((select count(*) from public.memberships m join public.payments p on p.id = m.payment_id where p.reference = 'ref-short'),
          0::bigint, 'and grants nothing');
select is(public.record_payment('ref-short', 1001, 250000, 'NGN', 'card', now(), '{}'), 'needs_review',
          'needs_review is never overwritten automatically');
select is(public.record_payment('ref-usd', 1002, 750000, 'NGN', 'card', now(), '{}'), 'unknown', 'unknown reference');
select is((select count(*) from public.admin_alerts where kind = 'unknown_reference'), 1::bigint, 'unknown reference alerted');
select public.start_payment('11111111-1111-1111-1111-111111111111', 'weekly', 'ref-cur', 'test');
select is(public.record_payment('ref-cur', 1003, 250000, 'USD', 'card', now(), '{}'), 'needs_review', 'wrong currency goes to review');

-- ---------- success, idempotent ----------
select is(public.record_payment('ref-1', 2001, 750000, 'ngn', 'card', now(), '{"id":2001}'), 'success', 'matching payment succeeds');
select is(public.record_payment('ref-1', 2001, 750000, 'NGN', 'card', now(), '{"id":2001}'), 'success', 'repeat is a no-op');
select is((select count(*) from public.memberships m join public.payments p on p.id = m.payment_id where p.reference = 'ref-1'),
          1::bigint, 'exactly one membership');
select ok((select ends_at - starts_at = interval '30 days' from public.memberships m join public.payments p on p.id = m.payment_id
            where p.reference = 'ref-1'), 'monthly plan lasts 30 days');

set local role authenticated;
select pg_temp.as_user('11111111-1111-1111-1111-111111111111');
select is(public.is_premium(), true, 'member is premium');
select is((select count(*) from public.tips t join public.fixtures f on f.id = t.fixture_id where f.api_fixture_id = 990301),
          1::bigint, 'member sees the pending premium tip');
select pg_temp.as_user('22222222-2222-2222-2222-222222222222');
select is(public.is_premium(), false, 'another signed-in user is not premium');
select is((select count(*) from public.tips t join public.fixtures f on f.id = t.fixture_id where f.api_fixture_id = 990301),
          0::bigint, 'signed-in non-member does not see it');
select is((select count(*) from public.payments), 0::bigint, 'nor anyone else''s payments');
reset role;
set local role anon;
select is(public.is_premium(), false, 'anonymous is not premium');
reset role;

-- ---------- stacking, a late success after abandoned, revoking ----------
select public.start_payment('11111111-1111-1111-1111-111111111111', 'weekly', 'ref-2', 'test');
select is(public.mark_abandoned('ref-2'), true, 'reconcile can abandon a stale pending payment');
select is(public.record_payment('ref-2', 2002, 250000, 'NGN', 'transfer', now(), '{}'), 'success',
          'a verified success after abandoned still settles');
select ok((select m2.starts_at = m1.ends_at
             from public.memberships m1 join public.payments p1 on p1.id = m1.payment_id and p1.reference = 'ref-1',
                  public.memberships m2 join public.payments p2 on p2.id = m2.payment_id and p2.reference = 'ref-2'),
          'a new plan stacks after the current one');

select public.revoke_membership((select id from public.payments where reference = 'ref-1'), 'refunded');
select ok((select m2.starts_at = m1.ends_at
             from public.memberships m1 join public.payments p1 on p1.id = m1.payment_id and p1.reference = 'ref-1',
                  public.memberships m2 join public.payments p2 on p2.id = m2.payment_id and p2.reference = 'ref-2'),
          'revoking does not move later memberships');
select throws_ok($$ select public.revoke_membership((select id from public.payments where reference = 'ref-1'), 'again') $$,
                 'P0002', null, 'a membership is revoked once');
select public.start_payment('11111111-1111-1111-1111-111111111111', 'weekly', 'ref-3', 'test');
select public.record_payment('ref-3', 2003, 250000, 'NGN', 'card', now(), '{}');
select ok((select m3.starts_at = m2.ends_at
             from public.memberships m2 join public.payments p2 on p2.id = m2.payment_id and p2.reference = 'ref-2',
                  public.memberships m3 join public.payments p3 on p3.id = m3.payment_id and p3.reference = 'ref-3'),
          'stacking ignores revoked memberships (after ref-2, the latest unrevoked)');

-- ---------- payments mode ----------
update public.app_settings set value = 'live' where key = 'payments_mode';
set local role authenticated;
select pg_temp.as_user('11111111-1111-1111-1111-111111111111');
select is(public.is_premium(), false, 'test memberships grant nothing in live mode');
reset role;
update public.app_settings set value = 'test' where key = 'payments_mode';

-- ---------- account deletion ----------
select public.start_payment('11111111-1111-1111-1111-111111111111', 'weekly', 'ref-4', 'test');
select is(public.can_delete_account('11111111-1111-1111-1111-111111111111'), false, 'deletion refused while a payment is pending');
select public.mark_abandoned('ref-4');
delete from auth.users where id = '11111111-1111-1111-1111-111111111111';
select results_eq($$ select (select count(*) from public.profiles where user_id = '11111111-1111-1111-1111-111111111111'),
                            (select count(*) from public.payments where user_id is null and reference like 'ref-%'),
                            (select count(*) from public.memberships where user_id is null) $$,
                  $$ values (0::bigint, 6::bigint, 3::bigint) $$, 'deleting an account keeps payments and memberships, user removed');
select is(public.record_payment('ref-4', 2004, 250000, 'NGN', 'card', now(), '{}'), 'needs_review',
          'a payment arriving for a deleted account goes to review');
select throws_ok($$ delete from public.payments $$, null, null, 'payments are never deleted');

select * from finish();
rollback;
