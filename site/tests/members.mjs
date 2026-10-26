// Signed-in leak test (Phase 5). Users are signed in through the Auth admin API (no inbox needed) and the
// session is sent the way @supabase/ssr stores it (cookie sb-<ref>-auth-token = "base64-" + base64url(session)).
//
// Local (default): against a production build reading the seeded local database (see tests/leak.mjs):
//   - a signed-in NON-member sees exactly what a visitor sees (no premium canary), on private, uncacheable pages;
//   - a member (test-mode membership granted through the service-role functions) sees premium content;
//   - after a member's request, an anonymous request for the same page still gets the visitor version.
//   LEAK_SERVICE_KEY defaults to the local secret key from `supabase status`.
//
// Remote (LEAK_REMOTE=1): against the deployed site, with LEAK_BASE, LEAK_SUPABASE_URL and LEAK_SERVICE_KEY for
// the cloud project, and LEAK_ACCESS_ID / LEAK_ACCESS_SECRET for the Cloudflare Access service token. A signed-in
// non-member's page carries an "Account" link; the anonymous request right after must never get that page from
// the edge cache.
import { execSync } from "node:child_process";

const REMOTE = process.env.LEAK_REMOTE === "1";
const BASE = process.env.LEAK_BASE ?? "http://localhost:4323";
const AUTH = process.env.LEAK_SUPABASE_URL ?? "http://127.0.0.1:54321";
const SERVICE = process.env.LEAK_SERVICE_KEY ?? localSecret();
const ACCESS = process.env.LEAK_ACCESS_ID
  ? { "CF-Access-Client-Id": process.env.LEAK_ACCESS_ID, "CF-Access-Client-Secret": process.env.LEAK_ACCESS_SECRET ?? "" } : {};
const CANARIES = [/Under 3\.5 goals/, /\b3\.17\b/, /\b61%/];
const failures = [];
const check = (ok, msg) => { if (!ok) failures.push(msg); };

function localSecret() {
  try {
    return /SECRET_KEY="([^"]+)"/.exec(execSync("npx supabase status -o env", { cwd: "..", encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }))?.[1];
  } catch { return undefined; }
}
if (!SERVICE) { console.error("No service key: set LEAK_SERVICE_KEY (or run local Supabase)."); process.exit(1); }

const admin = (path, init = {}) => fetch(`${AUTH}${path}`, {
  ...init, headers: { apikey: SERVICE, Authorization: `Bearer ${SERVICE}`, "Content-Type": "application/json", ...(init.headers ?? {}) },
});

/** A signed-in session for `email` (created if needed), as the site's cookie header. */
async function signedIn(email) {
  await admin("/auth/v1/admin/users", { method: "POST", body: JSON.stringify({ email, email_confirm: true }) });
  const link = await (await admin("/auth/v1/admin/generate_link", { method: "POST", body: JSON.stringify({ type: "magiclink", email }) })).json();
  const otp = link.email_otp ?? link.properties?.email_otp;
  const session = await (await admin("/auth/v1/verify", { method: "POST", body: JSON.stringify({ type: "email", email, token: otp }) })).json();
  if (!session.access_token) throw new Error(`could not sign in ${email}: ${JSON.stringify(session).slice(0, 200)}`);
  const ref = new URL(AUTH).hostname.split(".")[0];
  return { cookie: `sb-${ref}-auth-token=base64-${Buffer.from(JSON.stringify(session)).toString("base64url")}`, user: session.user.id };
}

const rpc = async (fn, args) => {
  const res = await admin(`/rest/v1/rpc/${fn}`, { method: "POST", body: JSON.stringify(args) });
  if (!res.ok) throw new Error(`${fn}: ${res.status} ${await res.text()}`);
  return res.json();
};

async function page(path, cookie) {
  const res = await fetch(BASE + path, { redirect: "manual", headers: { ...ACCESS, ...(cookie ? { cookie } : {}) } });
  return { res, body: await res.text() };
}

const ACCOUNT_LINK = /class="join-btn" href="\/account"/;

if (REMOTE) {
  // ---------- deployed site: the edge cache must never hand a signed-in page to anyone else ----------
  const { cookie } = await signedIn(process.env.LEAK_MEMBER_EMAIL ?? "leaktest@engrebodds.invalid");
  for (const path of ["/", "/predictions", "/banker-of-the-day", "/track-record"]) {
    const signed = await page(path, cookie);
    check(signed.res.status === 200, `${path}: signed-in status ${signed.res.status}`);
    check(ACCOUNT_LINK.test(signed.body), `${path}: signed-in request didn't get the signed-in page (served from cache? ${signed.res.headers.get("cf-cache-status")})`);
    check(signed.res.headers.get("cf-cache-status") !== "HIT", `${path}: signed-in response came from the edge cache`);
    check((signed.res.headers.get("cache-control") ?? "").includes("no-store"), `${path}: signed-in response not private`);
    const anon = await page(path);
    check(anon.res.status === 200, `${path}: anonymous status ${anon.res.status}`);
    check(!ACCOUNT_LINK.test(anon.body), `${path}: anonymous visitor got a signed-in page (cf-cache-status ${anon.res.headers.get("cf-cache-status")})`);
  }
} else {
  // ---------- local production build with the seeded database ----------
  const { body: list } = await page("/predictions");
  const B = list.match(/href="(\/predictions\/kano-kings-vs-enugu-eagles[^"]*)"/)?.[1];
  if (!B) { console.error("Seeded matches not found: load tests/seed.sql first (see tests/leak.mjs)."); process.exit(1); }
  const PAGES = ["/", "/predictions", "/banker-of-the-day", "/2-odds-today", "/over-under-2-5", "/bet-builder", B];

  const stamp = Date.now();
  const other = await signedIn(`nonmember-${stamp}@engrebodds.test`);
  for (const p of [...PAGES, "/account"]) {
    const { res, body } = await page(p, other.cookie);
    check(res.status === 200, `non-member ${p}: status ${res.status}`);
    for (const c of CANARIES) check(!c.test(body), `non-member ${p}: premium canary ${c} shown`);
    check((res.headers.get("cache-control") ?? "").includes("no-store"), `non-member ${p}: not private`);
    check(!/public/.test(res.headers.get("cloudflare-cdn-cache-control") ?? ""), `non-member ${p}: cacheable by the CDN`);
  }
  check(!(await page("/bet-builder", other.cookie)).body.includes('id="bdata"'), "non-member: builder probabilities embedded");

  const member = await signedIn(`member-${stamp}@engrebodds.test`);
  const profile = await admin("/rest/v1/profiles", { method: "POST", body: JSON.stringify({ user_id: member.user, adult_confirmed_at: new Date().toISOString() }) });
  check(profile.ok, `profile insert: ${profile.status}`);
  const ref = `eo_leak_${stamp}`;
  const [start] = await rpc("start_payment", { p_user: member.user, p_plan: "weekly", p_reference: ref, p_site_mode: "test" });
  check(await rpc("record_payment", { p_reference: ref, p_paystack_id: 1, p_amount: start.amount_kobo, p_currency: "NGN",
    p_channel: "card", p_paid_at: new Date().toISOString(), p_raw: {} }) === "success", "member payment not recorded");
  const m = await page(B, member.cookie);
  check(CANARIES.some((c) => c.test(m.body)), "member: premium tip not shown");
  check((m.res.headers.get("cache-control") ?? "").includes("no-store"), "member page not private");
  check((await page("/bet-builder", member.cookie)).body.includes('id="bdata"'), "member: builder data missing");
  const anonAfter = await page(B);
  for (const c of CANARIES) check(!c.test(anonAfter.body), `anonymous after a member request: canary ${c} shown`);

  // Responses that set a cookie (signing in) are never cacheable.
  const form = new URLSearchParams({ step: "code", email: `x-${stamp}@engrebodds.test`, code: "000000" });
  const signin = await fetch(`${BASE}/sign-in`, { method: "POST", redirect: "manual", body: form, headers: { Origin: BASE } });
  check((signin.headers.get("cache-control") ?? "").includes("no-store"), "sign-in POST response cacheable");
  const forged = await page("/account", "sb-127-auth-token=base64-eyJhY2Nlc3NfdG9rZW4iOiJmb3JnZWQifQ");
  check(forged.res.status === 302, `forged session cookie accepted (/account status ${forged.res.status})`);
}

if (failures.length) {
  console.error(`MEMBER LEAK TEST FAILED (${failures.length}):\n- ${failures.join("\n- ")}`);
  process.exit(1);
}
console.log(REMOTE ? `Remote member test passed against ${BASE}.` : "Member leak test passed: non-member sees the visitor view, member sees Premium, nothing personal is cacheable.");
