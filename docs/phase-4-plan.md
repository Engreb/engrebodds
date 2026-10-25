# Phase 4: the website (local first)

## Context
Phase 2 is finished and the database is in place. Tips, slips, grading and the track-record views exist, and publishing writes previews until `PUBLISH_TIPS` is switched on. Emmanuel isn't upgrading to API-Football Pro yet, so we proceed in sequence with the website. That's also the first point where he can click through the real product.

Decisions (25 Oct 2026):
- **Stack:** Astro, rendered on the server, on Cloudflare. It sends very little JavaScript, which keeps pages cheap on Nigerian mobile data, and it works well for search. Cloudflare's free plan allows commercial use.
- **Deployment:** local first. The site runs on Emmanuel's PC against the real cloud database. The public deploy waits until publishing is switched on (a separate approval).

The prototype `prototype/engrebodds.html` is the reference for pages, layout, copy and design: its colours, the Barlow Condensed, Figtree and IBM Plex Mono fonts, the fixture list ("ledger"), the slip card, the banker card, the match drawer, and the 18+ footer. The one known difference is that the prototype shows 1·X·2 bars on every match. Under the step-4 access rule, a locked match shows its teams, kick-off and a "Premium" chip instead, because the 1X2 split would give away the result tip.

## How locking works (server-side, no new trust)
- The site reads Supabase **only with the public (anon) key**, on the server. Row-level security already decides what a visitor can see:
  - free tips at any time;
  - Premium tips and slips only after grading;
  - predictions only for finished matches or matches with a free tip.

  Locked content never reaches the browser, not even hidden. Until Phase 5 nobody is a member, so every visitor sees the non-member view.
- **Migration 0013 adds `fixture_tip_status`**, a narrow view: per fixture, the number of tips, whether any are free, the banker flag, and slip availability per kind with its leg count and combined odds. It carries **no selections, probabilities or prices of tips**. This lets the site draw the "Premium" chip and "5 selections ready · 5.12" without exposing the tips themselves. A pgTAP test checks that it shows no selection.
- **Preview mode (local only):** with `SITE_PREVIEW=1`, the site also reads the latest `tip_previews` and today's predictions using a service key kept in the git-ignored `site/.env.local`. Every page then carries a "Preview: not published, not on the record" banner and a "View as member" toggle, as in the prototype. The build **refuses** to produce a production bundle while `SITE_PREVIEW` is set, and the preview code is only loaded in local dev. You'll paste the key yourself; I won't handle it.

## Additions requested 25 Oct (and how each is handled)
1. **Indexable match pages for locked matches** at `/predictions/{home}-vs-{away}-{yyyy-mm-dd}` (slugified team names; if two fixtures share a slug, the fixture id is appended).
   - **Content:** last-5 form, H2H, league position, goals for and against, and a short preview generated from a template over those facts. It uses **only historical facts**: no xG, no probabilities, no 1X2 and no tip hints.
   - **On the free plan,** form and H2H come from what's loaded (2022–24 plus recent results) and **league position is left out**, because current-season standings need Pro. The page says "updated when available" rather than showing stale tables as current.
2. **Search-matched URLs and titles:**
   - `/2-odds-today`, `/5-odds-today`, `/10-odds-today` and `/banker-of-the-day`;
   - `/bookmakers/sportybet`, titled "SportyBet predictions today", and the same pattern for each bookmaker;
   - `/sure-odds-today`, titled "Sure odds today?", with honest copy: no odds are sure, here is today's lowest-risk slip and its real chance. It replaces `/low-risk-picks`.

   The smoke test bans the claims **"guaranteed", "100% sure", "fixed match"** (case-insensitive) on every page, not the phrase "sure odds". CLAUDE.md says "never use the claim 'sure odds'"; this page uses the phrase only as a question it answers, so the rule still holds. Reword CLAUDE.md if you want it spelled out.
3. **Edge caching:**
   - **How:** Astro on Cloudflare runs as a Worker, which sits *in front of* the CDN cache, so caching is done in middleware with the Workers Cache API. Anonymous GET requests are served from cache. After 60 seconds a cached page is still served while a fresh copy renders in the background, and it is served stale for at most 10 minutes. Responses carry `Cache-Control: public, s-maxage=60, stale-while-revalidate=600`.
   - **Phase 5 bypass, designed now:** any request with the session cookie (`eo_session`) or an `Authorization` header skips the cache, and those responses are always `private, no-store` with `Vary: Cookie`. A member page can therefore never land in the shared cache. The cache key ignores query strings except those on a short allowlist (e.g. `league`).
   - **Effect on locking:** a tip that becomes visible at grading appears within 60 seconds, which is acceptable. The policy is a pure function with unit tests: which requests are cacheable and which headers they get.
4. **New pages:** `/how-our-predictions-work`, `/about` (with a contact address you'll give me), `/privacy` and `/terms`.
   - **The methodology page is plain and honest:** a Dixon-Coles goals model, 1xBet prices where they exist, the league gate, what "no market price" means, and why hit rate is meaningless without odds.
   - **The privacy and terms pages are drafts, marked "needs legal review".** You're UK-based serving Nigerian users, so the UK GDPR and Nigeria's Data Protection Act 2023 both apply. A lawyer should check them before Phase 5 collects any data.
5. **Search and analytics:**
   - **Built now:**
     - `/sitemap.xml`, generated on request and cached for 1 day;
     - `SportsEvent` JSON-LD on match pages;
     - canonical tags, where a dated page that is today points to its `-today` URL;
     - titles and descriptions on every page.
   - **At deploy, on the deploy checklist:** Cloudflare Web Analytics, which is cookie-free so no consent banner is needed, and Google Search Console with the sitemap submitted.
6. **WhatsApp share and OG images:**
   - The share button is a plain `wa.me` link that shares the page link and title, never locked selections.
   - OG images are generated on request (`workers-og`, cached for a day) for the banker and each slip. They show only what's already public: the match, leg count, combined odds and the chance all legs land, plus the pick itself only when it's free or graded.
7. **Fonts:** subsetted WOFF2 files self-hosted from `site/public/fonts/` (Latin plus **₦**, the dash and punctuation, made once with `pyftsubset` and committed; the fonts are OFL-licensed). Only Barlow Condensed is preloaded.
8. **Public proof of the record:** publishing the day's tips in public at publish time would give away Premium, so this uses **commit and reveal**.
   - **At each live publish:**
     - a canonical JSON of that run's tips and slips is written;
     - its **SHA-256 hash** is posted at once to a **public Telegram channel**. Telegram stamps posts on its own servers and marks edits, which a git commit date can't prove since it can be set to anything;
     - the hash is also committed to a public `engrebodds-record` repo.
   - **After grading:** the full JSON is committed and posted, so anyone can check it matches the hash posted before kick-off.
   - **Where it lives:** the publish job (Python), and only when `PUBLISH_TIPS=on`; preview runs never post. The track-record page links to the channel and the repo and explains how to verify.
   - **Your part:** create the channel, the bot and the repo, and paste the tokens into GitHub secrets. Until they exist, the step logs "proof not configured" and publishing carries on.
9. **Local kick-off time:** the server renders `<time datetime=…>` in WAT. A small script rewrites it to the visitor's time zone, with the zone shown (e.g. "15:00 BST"), only when that zone differs from WAT.

## Pages and URLs
Everything shown is server-rendered. Times are in WAT (Africa/Lagos). The "day" is the publishing day, 07:45 to 07:45 WAT.

| Page | URL | Content |
|---|---|---|
| Home | `/` | Hero, banker card, today's 3 free fixtures, record summary, plans, bookmaker chips |
| Predictions | `/predictions` and `/predictions/[yyyy-mm-dd]` | Yesterday/Today/Tomorrow tabs, league filter, team search, ledger grouped by league; finished matches show the score and Won/Lost |
| Match | `/predictions/{home}-vs-{away}-{date}` | Always: form, H2H, goals for and against, position (Pro), template preview, SportsEvent data. When visible: expected goals, likely score, all 4 slot tips, every market's probability and fair odds, 1xBet price with capture time, "no market price" label. Opens as a drawer from lists. |
| Banker of the day | `/banker-of-the-day` | Today's banker (locked until graded unless free), banker record, recent bankers, share button |
| 2 / 5 / 10 odds | `/2-odds-today`, `/5-odds-today`, `/10-odds-today` | Slip card: legs, combined 1xBet price with capture time, **chance all legs land**, copy-selections button, share button; locked view shows leg count and combined odds only |
| Sure odds today? | `/sure-odds-today` | Honest copy and the lowest-risk slip with its real chance |
| Methodology, about, privacy, terms | `/how-our-predictions-work`, `/about`, `/privacy`, `/terms` | See addition 4 |
| Market pages | `/gg-ng`, `/over-1-5`, `/over-under-2-5`, `/double-chance`, `/correct-score` | The strongest selection per match in that market from the predictions (free or finished matches visible, the rest locked) |
| Bet builder | `/bet-builder` | Members only, with a locked panel for now. In preview mode it works: target odds, minimum probability, market choice. Small client-side script. |
| Leagues | `/leagues`, `/leagues/[slug]` | Covered leagues with today's fixture counts; league page = the predictions ledger filtered |
| Tips by bookmaker | `/bookmakers/[slug]` (SportyBet, Bet9ja, 1xBet, BetKing, Betway, MSport, NairaBet) | Today's 5-odds slip as a selections list with a copy button. Prices are labelled **1xBet** (the only Nigerian price we store), with a "check your bookmaker's odds" note. No booking codes, no affiliate links. |
| Track record | `/track-record`, `/track-record.csv` | Hit rate **with** average odds, units and ROI (priced tips only, with the "no market price" count shown); cumulative profit chart; breakdowns by slot, banker, free and league; slip record; every tip. Nothing is filtered out. The CSV covers everything. |
| Pricing | `/pricing` | The 3 placeholder plans (₦2,500 / ₦7,500 / ₦18,000, no auto-renewal) with "Premium opens Aug 2027; until then the record is being proved in public". |
| Sign up | `/sign-up` | Placeholder only. The account form and Paystack are Phase 5. No form and no data collected now. |
| Responsible gambling | `/responsible-gambling` | Prototype copy and support links |

On every page:
- the 18+ footer and the not-a-bookmaker notice;
- page titles and descriptions for search;
- empty states that tell the truth, e.g. "Tips for today are published at 07:45 WAT" or "No graded tips yet: the record starts when publishing begins".

## Code layout
- `supabase/migrations/20261025000013_site.sql`: the `fixture_tip_status` view, plus `supabase/tests/06_site.sql`.
- `site/`: an Astro project (TypeScript, `@astrojs/cloudflare` adapter, no UI framework).
  - `src/styles/tokens.css`: prototype tokens and light/dark themes, copied from the prototype's CSS.
  - `src/layouts/Base.astro`: header, nav, footer, preview banner.
  - `src/components/`: `Ledger`, `FixtureRow`, `BankerCard`, `SlipCard`, `LockChip`, `StatsRow`, `RecordTable`, `ProfitChart` (inline SVG, as in the prototype), `MarketTable`.
  - `src/lib/db.ts`: anon Supabase client and typed queries.
  - `src/lib/preview.ts`: dev-only preview source.
  - `src/lib/day.ts`: publishing window and WAT formatting, matching `publishing_window` in `model/engrebodds_model/live/tips.py`.
  - `src/lib/markets.ts`: labels for `market_code|selection|line`, e.g. "Over 2.5 goals" or "Home or draw & over 1.5".
  - `src/lib/cache.ts`: the cache policy (pure) and `src/middleware.ts`, which applies it with the Cache API.
  - `src/lib/slug.ts`: match slugs. `src/lib/facts.ts`: form, H2H and the template preview, from facts only.
  - `src/pages/og/…`: OG images. `src/pages/sitemap.xml.ts`.
  - `src/scripts/`: local time, drawer, copy, and the builder (the only client JavaScript).
  - `src/pages/…`: the pages listed above.
- `model/engrebodds_model/live/proof.py`: commit-and-reveal (canonical JSON, SHA-256, Telegram post, repo commit). It is called by `publish` in live mode and by a reveal step after grading. It is skipped with a note when it isn't configured.
- `.github/workflows/tests.yml`: adds a site job (type check, unit tests, build).
- `CLAUDE.md` is untouched.

## Order of work (checkpoints with you)
1. **Migration 0013 and its test**, applied locally and then to the cloud.
2. **Scaffold** (fonts, tokens, layout, cache middleware), then **home, predictions and match pages** with facts, slugs, local time and SEO tags, all on real data. **Checkpoint:** you run it locally and click through, with preview mode on to see today's preview tips.
3. **Banker, slips (2/5/10, sure-odds-today), market pages, correct score,** plus share buttons and OG images.
4. **Track record and CSV, leagues, bookmakers, pricing, sign-up placeholder, responsible gambling, methodology, about, privacy, terms, bet builder, sitemap.** **Checkpoint:** a full click-through, desktop and phone width.
5. **Proof of record** (`proof.py` plus tests; inactive until it's configured and `PUBLISH_TIPS=on`).
6. **Tests and CI**, then commit and push. No deployment. The deploy checklist (Cloudflare project, domain, Web Analytics, Search Console, Telegram, record repo) is written into `docs/` for later.

## Tests
- **Unit (Vitest):**
  - publishing window and WAT formatting, checked against the Python cases;
  - market labels;
  - slip copy text;
  - track-record maths display (priced-only ROI, hit rate without voids).
- **Leak test (the important one):** run the site against local Supabase seeded with a free tip, a pending Premium tip, a graded Premium tip and a Premium slip. Fetch every page as an anonymous visitor and assert that the pending Premium selection, its probability and its price appear **nowhere** in the HTML. Then assert that the free and graded tips do appear. A second check confirms that preview mode can't be turned on in a production build.
- **Database:** `06_site.sql` checks that `fixture_tip_status` exposes no selections and respects grading; the existing 80 pgTAP tests keep passing.
- **Page smoke test:** every route returns 200 and contains the 18+ notice; no page contains "guaranteed", "100% sure" or "fixed match". Match pages of locked fixtures carry facts and JSON-LD but no tip, probability or xG. OG images of locked items show no pick.
- **Cache:** member-cookie and Authorization requests are never cacheable; anonymous GETs get the public headers; the query allowlist works.
- **Proof:** the canonical JSON is stable (same tips give the same hash); the reveal matches the hash; it is skipped cleanly when not configured; preview runs never post.
- **Slugs and facts:** slug collisions are handled; the template preview never mentions model numbers.
- **Weight:** the home page sends less than 30 KB of JavaScript (only the drawer, copy button and builder scripts).

## Verification
1. `supabase db reset` locally, then the pgTAP, model, ingest and site tests all pass.
2. `npm run dev` in `site/` against the cloud database (anon key): today's real fixtures, locked chips, empty-but-honest record.
3. The same with `SITE_PREVIEW=1`: today's 84 preview fixtures with their 4 slots, free picks, and the "View as member" toggle.
4. Phone-width check (375 px) in the browser pane, light and dark.

## Not in this step
- the public deploy and domain (when publishing goes on, a separate approval);
- accounts, members and Paystack (Phase 5);
- WhatsApp alerts, affiliate links and booking codes (later).
