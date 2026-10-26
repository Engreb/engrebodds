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
- [ ] Anonymous pages are cached by the Worker cache (60 s, stale for up to 10 min). Check that `Cloudflare-CDN-Cache-Control: public, max-age=60, stale-while-revalidate=600` appears on `/predictions`.
- [ ] **Before Phase 5 (accounts),** add a Cache Rule (Caching → Cache Rules) named "Members bypass", with:
  - Expression: `(http.cookie contains "eo_session=") or (len(http.request.headers["authorization"]) > 0)`
  - Cache eligibility: **Bypass cache**

  The site already marks those responses `private, no-store`. The rule stops the cache from answering such a request before the Worker runs.

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
