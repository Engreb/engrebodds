# Deploy checklist (the public site)

The site runs locally today. It goes public only when publishing is switched on (`PUBLISH_TIPS=on`), which waits on API-Football Pro, a clean shadow record and Emmanuel's approval of each league. Work through this list in order then.

## 0. Private deploy (before launch)
The site can run privately on Cloudflare's free `*.workers.dev` address, behind Cloudflare Access, so only approved emails can open it. While `SITE_INDEXING` is `"off"` in `site/wrangler.jsonc` (the default), every page is `noindex` and `robots.txt` blocks all crawlers. A deployed build never has preview mode, so it shows exactly what the public would see.
- [ ] Create a free Cloudflare account. In `site/`, run `npx wrangler login`.
- [ ] `npm run build`, then `npx wrangler deploy`. Note the `https://engrebodds.<subdomain>.workers.dev` address it prints.
- [ ] Workers & Pages → engrebodds → Settings → Domains & Routes → workers.dev → **Enable Cloudflare Access**. Then, in Zero Trust → Access → Applications, edit its policy to **Include → Emails** with the addresses allowed in.
- [ ] Open the address in a private window: it must ask for a login code before showing anything.

## 1. Before deploying
- [ ] **Domain:** buy it (e.g. `engrebodds.com`), then set the same address in `site/astro.config.mjs` (`site`) and `site/public/robots.txt`.
- [ ] **Contact:** set `CONTACT_EMAIL` in `site/src/lib/site.ts` (the About page shows a "coming soon" line until then).
- [ ] **Legal:** a lawyer reviews `/privacy` and `/terms` (UK GDPR, Nigeria Data Protection Act 2023, Nigerian state gambling rules) and fills in the bracketed gaps. Remove the "Draft" notices.
- [ ] **Tests:** all CI jobs pass, including the site's leak test.

## 2. Cloudflare Worker
Emmanuel does the logins; nothing here needs a secret in the repo (the Supabase key in `wrangler.jsonc` is the public, publishable one).
- [ ] Create a free Cloudflare account and add the domain (Cloudflare becomes its DNS).
- [ ] In `site/`, run `npx wrangler login`, then `npm run build` and `npx wrangler deploy`. **Never build with `SITE_PREVIEW` set**; the build refuses anyway.
- [ ] Add the domain to the Worker (Workers & Pages → engrebodds → Settings → Domains & Routes).
- [ ] Check: the home page loads, `/sitemap.xml` lists today's matches, and `/og/banker.png` is an image.

## 3. Caching
- [ ] Anonymous pages are cached by the Worker cache (60 s, stale for up to 10 min). Cloudflare consumes the Worker's `Cloudflare-CDN-Cache-Control` header at the edge, so check `CF-Cache-Status: HIT` on a second request to `/predictions` instead.
- [ ] **Before anyone signs in on a deployed site,** add a Cache Rule (Caching → Cache Rules) named "Signed-in bypass", with:
  - Expression: `(http.cookie contains "-auth-token") or (len(http.request.headers["authorization"]) > 0)`
  - Cache eligibility: **Bypass cache**

  Required: on 26 Oct the live site served a cached page to a request carrying a session cookie. The site already marks every signed-in response `private, no-store`; the rule stops the cache from answering before the Worker runs.
- [ ] **No edge-TTL override:** no other Cache Rule may set an Edge TTL or "ignore cache-control" for this site. The Worker's own headers must decide.
- [ ] Run the remote member test (section 7).

## 4. Analytics and search
- [ ] **Cloudflare Web Analytics:** turn it on for the domain. It is cookie-free, so no consent banner is needed; the privacy page already describes it.
- [ ] **Google Search Console:** verify the domain (DNS TXT record) and submit `https://<domain>/sitemap.xml`.
- [ ] Share a match page and `/banker-of-the-day` on WhatsApp to check the preview card.

## 5. Proof of record (Telegram and GitHub)
Needed before the first live publish. Until it's set up, the publish and proof jobs log "not configured" and keep the hashes in the database only.
- [ ] **Telegram:**
  1. Create a public channel (e.g. `@engrebodds_record`).
  2. Create a bot with @BotFather and copy its token.
  3. Add the bot to the channel as an admin that can post.
- [ ] **GitHub:**
  1. Create a public repo `engrebodds-record` with a README explaining how to check a day: the SHA-256 of `runs/<date>/<time>.json` must equal `hashes/<date>/<time>.sha256`, posted before kick-off.
  2. Create a fine-grained personal access token with **Contents: read and write** on that repo only.
- [ ] **In this repo's GitHub settings** (Settings → Secrets and variables → Actions):
  - Secrets: `TELEGRAM_BOT_TOKEN`, `RECORD_REPO_TOKEN`
  - Variables: `TELEGRAM_CHAT_ID` (e.g. `@engrebodds_record`), `RECORD_REPO` (e.g. `Engreb/engrebodds-record`)
- [ ] Set the `PROOF` links in `site/src/lib/site.ts` (channel URL and repo URL), so the track record page links to them.
- [ ] After the first live publish, check that the hash is in the channel and repo before the first kick-off. After grading, check that the JSON is revealed and its SHA-256 matches.

## 6. Switching publishing on
- [ ] Enable each league with `model:league_tips <api_id> on` (it refuses leagues that fail the shadow gate).
- [ ] Set the free-tip priority leagues with `model:league_priority <api_id> <n>`.
- [ ] Set the repo variable `PUBLISH_TIPS=on`.
- [ ] Set `SITE_INDEXING` to `"on"` in `site/wrangler.jsonc`, change the leak test's `INDEXING_OFF` to `false`, redeploy, and remove Cloudflare Access from the public domain.
- [ ] Watch the first 06:45 UTC run: tips, slips and the proof hash.

## 7. Accounts and payments (Phase 5, docs/phase-5-plan.md)
Built and tested locally. In the cloud, migration 0015 and the five Edge Functions are live, but with no secrets they refuse everything. Do these in order.

**Before any live plans (step 0):** ask Paystack and Flutterwave, in writing, whether a football predictions subscription is an accepted business category, and whether a UK-based owner without a Nigerian company can be a merchant. Keep the replies with the legal advice.

**Sign-in on the private deploy (`PAYMENTS=test`):**
- [ ] The Cache Rule in section 3 is in place.
- [ ] **Turnstile:** Cloudflare dashboard → Turnstile → add a widget for the `workers.dev` hostname, then copy the site key and secret.
  - In Supabase → Authentication → Attack Protection, enable CAPTCHA with provider **Turnstile** and paste the **secret**.
  - Put the **site key** in `site/wrangler.jsonc` as `TURNSTILE_SITE_KEY`.
- [ ] **Supabase → Authentication:**
  - URL Configuration: set Site URL to the deployed address.
  - Email Templates → Magic Link: subject "Your Engrebodds sign-in code", body from `supabase/templates/sign-in-code.html` (it shows `{{ .Token }}`).
  - Rate limits: keep the defaults (they cap code requests and checks).
- [ ] **Email:** Supabase's built-in sender is enough for your own testing (a few emails an hour). For anyone else, set **Resend** SMTP in Authentication → Emails. Without a verified domain, Resend only delivers to your own address.
- [ ] **Cloudflare rate-limit rule** (Security → WAF → Rate limiting, one free rule): requests to `/sign-in` with method POST, at most 10 per minute per IP → Block for 10 minutes.
- [ ] Set `"PAYMENTS": "test"` in `site/wrangler.jsonc`, then `npm run build` and `npx wrangler deploy`.
- [ ] **Remote member test:** create an Access **service token** (Zero Trust → Access → Service Auth), add it to the Access policy (Include → Service Token), then in `site/`:
  ```
  LEAK_REMOTE=1 LEAK_BASE=https://engrebodds.engrebodds-site.workers.dev LEAK_SUPABASE_URL=https://xmgjrcgyuebsnvyvyqdx.supabase.co LEAK_SERVICE_KEY=<secret key> LEAK_ACCESS_ID=<id> LEAK_ACCESS_SECRET=<secret> node tests/members.mjs
  ```
  It signs in a test user (`leaktest@engrebodds.invalid`, no payments), then checks that a signed-in request is never answered from the edge cache, and that an anonymous request straight afterwards gets the visitor page.

**Test payments (when you have a Paystack account):**
- [ ] Function secrets (Supabase → Edge Functions → Secrets, or `npx supabase secrets set --project-ref xmgjrcgyuebsnvyvyqdx …`): `PAYSTACK_SECRET_KEY` (an `sk_test_` key), `SITE_URL` (the deployed address) and `RECONCILE_TOKEN` (a long random string). **Never set `PAYSTACK_BASE` in the cloud.**
- [ ] The same `RECONCILE_TOKEN` as a GitHub Actions secret (the daily 03:11 UTC reconcile job).
- [ ] Paystack dashboard → Settings → API keys & webhooks: set the test webhook URL to `https://xmgjrcgyuebsnvyvyqdx.supabase.co/functions/v1/paystack-webhook`.
- [ ] Pay with a Paystack test card, and with a test bank transfer: Premium starts, the account page shows "Payment received", and `payment_events` has the event.

**Before `PAYMENTS=live` (separate approval; see docs/phase-5-plan.md):**
- [ ] The written replies from step 0, business verification, legal advice, and the reviewed privacy and terms pages.
- [ ] **Alert emails** to Emmanuel: immediate for `needs_review`, refund and dispute; a daily summary otherwise. A test alert must have been delivered.
- [ ] `payments_mode` set to `live` in `app_settings`, `PAYMENTS_LIVE=yes` and an `sk_live_` key in the function secrets, and `"PAYMENTS": "live"` in `wrangler.jsonc`, all together.
