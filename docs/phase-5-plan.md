# Phase 5: accounts and payments (test mode), revised

## Context
Phase 4 is done and privately deployed (`engrebodds.engrebodds-site.workers.dev`, noindex). Emmanuel chose to build Phase 5 now, before the record exists. This phase builds the complete member journey **in Paystack test mode**:
- sign in, choose a plan, pay with test cards;
- get Premium for the plan's days and see everything;
- expire with no auto-renewal, or delete the account.

A `PAYMENTS` switch keeps it off on the public site.

**Live payments are a separate, later approval.** They need:
- Paystack's written answer (below);
- business verification;
- legal advice on selling tips into Nigeria;
- the reviewed privacy and terms pages;
- a track record worth paying for.

Decisions (26 Oct 2026): sign-in by a **6-digit email code** (Supabase Auth OTP; no passwords). **No Paystack account yet**, so everything is built against test mode and signed fake webhooks.

## Step 0: Emmanuel, in writing, before any live plans
Ask **Paystack and Flutterwave** in writing:
1. Is a football predictions subscription (statistical tips, no betting handled) an accepted business category?
2. Can a UK-based owner, without a Nigerian company, open a merchant account? If not, what's needed?

Keep the replies with the legal advice. They decide whether live payments go ahead at all, and with which provider.

## Design
### Secrets
- The Cloudflare Worker holds **no secrets** (public key only), as today.
- The **Paystack secret key** lives only in **Supabase Edge Function secrets**, set by Emmanuel.
- The reconcile job uses its own `RECONCILE_TOKEN`, never the service key.

### Data (migration 0015)
- **`app_settings`** (service-role writes): `payments_mode` = `test` | `live`, starting at `test`. Read through `payments_mode()`, a small security-definer function.
- **`plans`:** weekly ₦2,500 / 7 days, monthly ₦7,500 / 30 days, 3 months ₦18,000 / 90 days. Amounts in kobo, plus an `active` flag; public read.
- **`profiles`:**
  - columns: `user_id` (references `auth.users`, **on delete cascade**), `adult_confirmed_at` (required), `created_at`;
  - nothing else is collected; the email address stays in Auth.
- **`payments`:**
  - columns: `reference` (unique), `user_id` (**on delete set null**: records are kept), `plan_id`, `mode`, `expected_amount_kobo`, `expected_currency` (both set by `pay-start` when the payment is created), `amount_kobo`, `currency`, `status` (`pending` | `success` | `failed` | `abandoned` | `needs_review`), `paystack_id`, `channel`, `paid_at`, `raw` jsonb;
  - **`raw` is stripped to the accounting fields only**: Paystack id, reference, amount, currency, status, channel, paid_at, fees and gateway response. No card details, customer data or IP addresses;
  - never deleted.
- **`payment_events`:** every webhook event, kept: `event` (`charge.success`, `refund.processed`, `charge.dispute.create`, …), `reference`, `payment_id` (if matched), the same **stripped** `raw`, `received_at`. Never deleted.
- **`memberships`:**
  - columns: `user_id` (**on delete set null**), `payment_id` (unique), `mode`, `starts_at`, `ends_at`, `revoked_at`, `revoke_reason`;
  - a new plan **stacks**: it starts at the later of now and the end of the latest **non-revoked** membership in the same mode;
  - **revoking doesn't shift memberships stacked after it**: they keep their dates, so a gap can appear, and the docs say so. Emmanuel can grant a corrected membership by hand if a refund case needs it;
  - never deleted; the guard allows only `revoked_at`, `revoke_reason`, and `user_id` becoming null.
- **`admin_alerts`:** `kind`, `payment_id`, `detail` jsonb, `created_at`, `resolved_at`. Service-role only. Email alerts later.
- **`is_premium()`:** true when there is a membership for `auth.uid()` with `mode = payments_mode()`, `revoked_at is null` and now between `starts_at` and `ends_at`. Every existing tips, slips and predictions policy already calls it.
- **Service-role functions:**
  - **`record_payment(reference, paystack_id, amount, currency, channel, paid_at, raw)`:**
    - **only `success` is terminal**: a repeat on a `success` payment does nothing;
    - **a verified success replaces `pending`, `abandoned` or `failed`**, for example a late payment after reconcile marked it abandoned;
    - `needs_review` is never overwritten automatically;
    - amount and currency must equal the payment's **stored expected values**;
    - a mismatch sets `needs_review`, writes an `admin_alerts` row and grants nothing;
    - **a success for a deleted user** (`user_id` null) also goes to `needs_review` with an alert, and grants nothing;
    - otherwise it marks the payment `success` and creates the membership with the payment's mode.
  - **`revoke_membership(payment_id, reason)`:** for after a manual refund or a lost dispute.
  - **`mark_abandoned(reference)`.**
- **RLS:**
  - users read their own profile, payments and memberships;
  - users insert or update only their own profile;
  - everything else goes through the functions or the service role.

### Edge Functions (`supabase/functions/`, Deno)
Shared Paystack client: `_shared/paystack.ts`.
- **`pay-start`** (validated user):
  1. checks the 18+ confirmation and that the plan is active;
  2. creates a `pending` payment with the expected amount and currency and the current `payments_mode()`;
  3. calls `transaction/initialize` (NGN; card, bank transfer, USSD; callback to `/account`);
  4. returns the checkout URL.

  It refuses a live key (`sk_live_`) unless both `payments_mode = live` and `PAYMENTS_LIVE=yes` are set.
- **`paystack-webhook`:**
  1. verifies the `x-paystack-signature` HMAC-SHA512 of the raw body (anything unsigned or invalid gets 401);
  2. stores the event in `payment_events`;
  3. on `charge.success`, re-checks with `transaction/verify/<reference>`, then calls `record_payment`;
  4. on `refund.processed` and `charge.dispute.create`, writes an `admin_alerts` row so Emmanuel can review and revoke;
  5. answers 200 quickly.
- **`pay-verify`** (validated user, own reference only): the same verify-and-record for the return from checkout. It's idempotent with the webhook.
- **`pay-reconcile`** (daily, `RECONCILE_TOKEN`): pending payments older than 24 hours are checked with Paystack, then recorded as success (through `record_payment`) or marked abandoned. It's called from the Ingest workflow on a new daily schedule line.
- **`delete-account`** (validated user; the **sign-in time from the `amr` claim**, not `iat` since refreshes renew that, must be within the last 10 minutes, otherwise "sign in again"):
  - **refuses while the user has a `pending` payment** ("finish or wait for your payment first"); a success that still arrives later for a deleted user goes to `needs_review`, as above;
  - deletes the Auth user;
  - the profile cascades, while payments and memberships keep their rows with `user_id` null;
  - the page warns first that remaining Premium days are forfeited.

### Site
- **Auth:** `@supabase/ssr` with Astro cookies.
  - **Identity:** the middleware validates the session with **`getClaims()`**, which checks the signature and falls back to `getUser()`. It **never trusts `getSession()`**.
  - **Pages:** `/sign-in` (email, then the 6-digit code), `/account` (status and end date, payments, sign out, delete account, return from checkout) and `/sign-up` (plan, 18+ confirmation, pay).
  - **Pricing:** the buttons go to `/sign-up?plan=…`.
- **Abuse protection:**
  - **Cloudflare Turnstile** on sign-in, using Supabase Auth's own captcha check, so the code request is refused without a valid token;
  - Supabase Auth **rate limits** on OTP requests and verifications;
  - a Cloudflare **rate-limiting rule** on the sign-in path (free plan: one rule).
- **Member reads:** pages read with the user's session, so RLS decides what members see. `Day.member` comes from `is_premium()`, and the existing member view applies.
- **Caching:**
  - any `-auth-token` cookie counts as a session, and session requests stay `private, no-store`;
  - **any response with `Set-Cookie` is never cached**, enforced in the middleware;
  - the **Cache Rule** must bypass for `http.cookie contains "-auth-token"`, and there must be **no edge-TTL override**: the Worker's own `Cloudflare-CDN-Cache-Control` must decide. The deploy checklist says how to confirm it in the dashboard.
- **`PAYMENTS` switch** (wrangler var): `off` (today's placeholders; production until launch), `test` (the full flow, with a "Test mode: no real money" banner), and later `live`.
- **Mode check:** before showing checkout, the site checks that the database's `payments_mode()` matches its `PAYMENTS` value. **A mismatch shows an error state ("Payments are temporarily unavailable"), never checkout.** `pay-start` refuses a mismatch too.
- **Privacy draft:** updated for email, the 18+ confirmation, payment records, Paystack and Turnstile as processors, and account deletion. It also states the **retention period for payment records**: 6 years after the payment (UK accounting rules), kept without the account after deletion, to be confirmed by the lawyer or accountant. It stays marked for legal review.

### Emmanuel's setup (when we reach it)
- **Resend SMTP** in Supabase Auth, with the code template using `{{ .Token }}`. Until a domain is verified it delivers only to his own address, which is fine for testing.
- **Turnstile:** create the site key and secret; the secret goes into Supabase Auth captcha settings.
- **Paystack test keys** into Edge Function secrets, and the webhook URL into Paystack's dashboard.
- **Cloudflare:** the Cache Rule and the sign-in rate-limit rule. A **Cloudflare Access service token**, so the remote leak test can pass the login wall.

## Order of work (checkpoints)
1. **Migration 0015 and pgTAP** (`07_accounts.sql`):
   - RLS on every new table;
   - `is_premium` before, during and after a membership, **in each payments mode** (a test membership grants nothing when the mode is `live`, and the other way round), and when revoked;
   - stacking, **ignoring revoked memberships**, and revoking not moving later memberships;
   - `record_payment`:
     - idempotent on `success`;
     - **abandoned by reconcile, then a late `charge.success`, gives success and a membership**;
     - a mismatch goes to `needs_review` with an alert and no membership;
     - a success for a deleted user goes to `needs_review` with an alert;
   - `revoke_membership` and `mark_abandoned`;
   - account deletion: profile gone, payment and membership rows kept with a null user;
   - a member sees pending premium tips while a signed-in non-member doesn't.
2. **Site auth:** `getClaims` middleware, sign-in with Turnstile, account page, sign out, session-aware reads, the `Set-Cookie` no-cache rule. **Checkpoint:** Emmanuel signs in locally (local inbox, Turnstile's test keys).
3. **Edge Functions and Deno tests:**
   - signature checks;
   - an expected-amount mismatch giving `needs_review` plus an alert;
   - idempotency, and a late success after abandoned;
   - the live-key refusal, and the mode-mismatch refusal;
   - refund and dispute events making alerts;
   - `raw` stripped to the accounting fields;
   - reconcile (success and abandoned);
   - delete-account (the `amr` recency check, and refusal while a payment is pending).
4. **Sign-up, checkout and deletion** behind `PAYMENTS=test`. **Checkpoint:** a full local run with a signed fake webhook; then a real Paystack test payment once Emmanuel has keys.
5. **Tests and CI:**
   - **Local leak test, extended:**
     - a signed-in non-member sees exactly the visitor view;
     - a member sees Premium;
     - member and `Set-Cookie` responses are uncacheable.
   - **Remote mode** (`LEAK_BASE` set to the private deploy, plus the Access service token), against a test member in the cloud:
     - a **member request, then an anonymous one**;
     - the anonymous response must show no Premium content, whatever its `cf-cache-status` says;
     - the member response must not have been served from cache.
   - **CI:** the site job also starts Auth; the Deno function tests are added.
6. **Cloud:** the migration (MCP, versions aligned) and the functions go up; Emmanuel adds the secrets and Cloudflare rules; the private site is redeployed with `PAYMENTS=test`; the remote leak test runs. Then commit and push, and update the deploy checklist.

## Critical files
- `supabase/migrations/2026102600015_accounts.sql`, `supabase/tests/07_accounts.sql`
- `supabase/functions/{pay-start,paystack-webhook,pay-verify,pay-reconcile,delete-account}/index.ts`, `supabase/functions/_shared/paystack.ts`, with tests
- **Site:**
  - `src/lib/auth.ts`;
  - `src/lib/db.ts`: `loadDay` takes the request's client;
  - `src/lib/source.ts`;
  - `src/lib/cache.ts`;
  - `src/middleware.ts`;
  - pages `sign-in`, `account`, `sign-up` and `pricing`;
  - `tests/leak.mjs`: the member and remote modes;
  - `wrangler.jsonc`: `PAYMENTS`.
- `.github/workflows/tests.yml` and `ingest.yml` (the reconcile line), `docs/deploy-checklist.md`

## Verification
- pgTAP, model, ingest, site unit, local and remote leak tests and Deno tests all pass, locally and in CI where applicable.
- **Locally:**
  - sign in with Turnstile test keys and the code from the local inbox;
  - pay with a signed fake `charge.success`: Premium appears, and a repeat changes nothing;
  - a wrong amount gives `needs_review` plus an alert;
  - a refund event gives an alert, then `revoke_membership` ends Premium;
  - switching `payments_mode` to `live` hides test Premium;
  - a stale pending payment is reconciled to abandoned;
  - deleting the account keeps payment rows with a null user.
- **With Emmanuel's test keys:** a real test checkout (card and transfer) on the private deploy, the webhook delivered, Premium granted, and the remote leak test passing.

## Required before `PAYMENTS=live` (part of that later approval)
- **Alert emails to Emmanuel:**
  - `admin_alerts` rows of kind `needs_review`, `refund` and `dispute` are emailed **immediately** (sent by the Edge Function that creates them, through Resend);
  - every other alert goes into a **daily summary**, sent by the reconcile job.
  - An `emailed_at` column records delivery, and a failed send is retried by the next reconcile run.
- **Checks on the live switch:** the live switch refuses unless alert email is configured and a test alert has been delivered. The deploy checklist records that check.

## Not in this phase
Live payments (separate approval, above), auto-renewal (never), WhatsApp alerts, expiry reminder emails, automatic refunds (refunds are done by hand in Paystack, then `revoke_membership`).
