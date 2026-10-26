// Leak test: fetch every page of a PRODUCTION build as an anonymous visitor and prove that pending premium
// content never reaches the browser, while free and graded content does.
//
// Needs: local Supabase (supabase start) reset and seeded with tests/seed.sql, then
//   npm run build -- --mode test && npm run preview -- --mode test --port 4323
//   node tests/leak.mjs            (LEAK_BASE overrides http://localhost:4323)
// CI does all of this in .github/workflows/tests.yml.

const BASE = process.env.LEAK_BASE ?? "http://localhost:4323";
const failures = [];
const check = (ok, msg) => { if (!ok) failures.push(msg); };

async function get(path, init = {}) {
  const res = await fetch(BASE + path, { redirect: "manual", ...init });
  const body = res.headers.get("content-type")?.startsWith("image/") ? "" : await res.text();
  return { res, body };
}

// Canaries: fixture B's premium picks (tests/seed.sql). None may appear anywhere a visitor can fetch.
const CANARIES = [/Under 3\.5 goals/, /\b3\.17\b/, /\b61%/, /\b43%/, /\b0\.6123\b/, /\b0\.4321\b/];
const BANNED = /guaranteed|100% sure|fixed match/i;

const { res: home, body: predictions } = await get("/predictions");
if (home.status !== 200) {
  console.error(`Site not reachable at ${BASE} (status ${home.status}).`);
  process.exit(1);
}
const matchUrl = (team) => predictions.match(new RegExp(`href="(/predictions/${team}[^"]*)"`))?.[1];
const A = matchUrl("lagos-lions-vs-abuja-stars"), B = matchUrl("kano-kings-vs-enugu-eagles"), C = matchUrl("ibadan-city-vs-benin-rovers");
if (!A || !B || !C) {
  console.error("Seeded matches not found on /predictions. Was tests/seed.sql loaded inside today's publishing window? " +
    "(It can't be between about 05:25 and 06:45 UTC; run the test outside that band.)");
  process.exit(process.env.CI ? 1 : 2);
}

const PAGES = ["/", "/predictions", "/banker-of-the-day", "/2-odds-today", "/5-odds-today", "/10-odds-today", "/sure-odds-today",
  "/gg-ng", "/over-1-5", "/over-under-2-5", "/double-chance", "/correct-score", "/bet-builder", "/leagues",
  "/leagues/nigeria-test-premier-league", "/track-record", "/pricing", "/sign-up", "/responsible-gambling",
  "/how-our-predictions-work", "/about", "/privacy", "/terms", "/bookmakers/sportybet", "/bookmakers/1xbet", A, B, C];
const FILES = ["/track-record.csv", "/track-record-slips.csv", "/sitemap.xml", "/robots.txt"];
// Until launch nothing may be indexed (SITE_INDEXING=off in wrangler.jsonc).
const INDEXING_OFF = true;

const html = {};
for (const p of [...PAGES, ...FILES]) {
  const { res, body } = await get(p);
  html[p] = body;
  check(res.status === 200, `${p}: status ${res.status}`);
  for (const c of CANARIES) check(!c.test(body), `${p}: premium canary ${c} is in the response`);
  if (PAGES.includes(p)) {
    check(body.includes("18+"), `${p}: no 18+ notice`);
    check(!BANNED.test(body), `${p}: contains a banned claim`);
  }
}

// Free and graded content is visible.
const TIPS_HEADING = />Our tips<\/h2>/;
check(html[A].includes("1.45") && TIPS_HEADING.test(html[A]), "free match: tips not shown");
check(html["/"].includes("Lagos Lions"), "home: free tips missing");
check(TIPS_HEADING.test(html[C]) && /res won/.test(html[C]), "graded premium match: tips not shown after grading");
check(html["/track-record"].includes("Ibadan City"), "track record: graded tip missing");
check(html["/track-record.csv"].includes("Ibadan City"), "CSV: graded tip missing");
check(html["/5-odds-today"].includes("Lagos Lions v Abuja Stars"), "free slip: legs not shown");

// Locked content shows that it exists, never what it is.
check(html[B].includes("SportsEvent") && html[B].includes("Kano Kings"), "locked match: facts or JSON-LD missing");
check(!html[B].includes("xG") && !html[B].includes("Every market") && !TIPS_HEADING.test(html[B]), "locked match: model content shown");
check(html["/banker-of-the-day"].includes("Kano Kings") && html["/banker-of-the-day"].includes("Pick hidden"), "locked banker: wrong card");
check(html["/2-odds-today"].includes("Premium slip") && !html["/2-odds-today"].includes("Lagos Lions v Abuja Stars"),
  "locked slip: selections shown");
check(!html["/bet-builder"].includes('id="bdata"'), "bet builder: probabilities embedded for a visitor");
check(!/Preview\./.test(html["/"]), "production build shows the preview banner");

// Edge-cache policy as served.
const anon = await get("/predictions");
check(/public/.test(anon.res.headers.get("cloudflare-cdn-cache-control") ?? ""), "anonymous page: no public CDN cache header");
const member = await get("/predictions", { headers: { cookie: "eo_session=x" } });
check((member.res.headers.get("cache-control") ?? "").includes("no-store"), "session cookie: response not private");
check(!/public/.test(member.res.headers.get("cloudflare-cdn-cache-control") ?? ""), "session cookie: response cacheable by the CDN");
const tracked = await get("/predictions?utm_source=wa");
check(tracked.res.status === 301 && tracked.res.headers.get("location") === "/predictions", "unknown query parameter not dropped");

// Nothing indexable before launch.
if (INDEXING_OFF) {
  check(/Disallow: \/\s*$/m.test(html["/robots.txt"]), "robots.txt doesn't block crawlers");
  check(/noindex/.test(anon.res.headers.get("x-robots-tag") ?? ""), "no X-Robots-Tag noindex header");
  check(html["/"].includes('<meta name="robots" content="noindex">'), "home page has no noindex meta tag");
}

// JavaScript weight on the home page.
let js = 0;
for (const src of new Set([...html["/"].matchAll(/<script[^>]+src="([^"]+)"/g)].map((m) => m[1]))) {
  js += (await (await fetch(BASE + src)).arrayBuffer()).byteLength;
}
js += [...html["/"].matchAll(/<script(?![^>]*src=)(?![^>]*ld\+json)[^>]*>([\s\S]*?)<\/script>/g)].reduce((n, m) => n + m[1].length, 0);
check(js < 30_000, `home page sends ${js} bytes of JavaScript (limit 30000)`);

if (failures.length) {
  console.error(`LEAK TEST FAILED (${failures.length}):\n- ${failures.join("\n- ")}`);
  process.exit(1);
}
console.log(`Leak test passed: ${PAGES.length + FILES.length} responses, no premium content leaked; home JS ${js} bytes.`);
