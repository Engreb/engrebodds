// Edge Function logic with a fake database and a fake Paystack. The database's own money rules
// (record_payment, stacking, modes) are tested in supabase/tests/07_accounts.sql; here: verification, routing, refusals.
import { assert, assertEquals, assertRejects } from "jsr:@std/assert@1";
import { type Db, deleteAccount, HttpError, payStart, payVerify, reconcile, webhook } from "./handlers.ts";
import { type Mode, Paystack, sign, strip, type Transaction } from "./paystack.ts";

const SECRET = "sk_test_abc123";

class FakeDb implements Db {
  mode: Mode = "test";
  payments = new Map<string, { user_id: string | null; status: string; expected: number; created_at: string }>();
  memberships: string[] = [];
  events: { event: string; reference: string | null; raw: Record<string, unknown> }[] = [];
  alerts: { kind: string; reference: string | null }[] = [];
  deleted: string[] = [];
  paymentsMode = () => Promise.resolve(this.mode);
  startPayment(user: string, plan: string, reference: string, siteMode: string) {
    if (siteMode !== this.mode) return Promise.reject(new Error("payments mode mismatch"));
    if (plan !== "monthly") return Promise.reject(new Error("unknown or inactive plan"));
    this.payments.set(reference, { user_id: user, status: "pending", expected: 750000, created_at: new Date().toISOString() });
    return Promise.resolve({ amount_kobo: 750000, currency: "NGN", mode: this.mode });
  }
  recordPayment(a: { reference: string; amount: number; currency: string }) {        // mirrors record_payment's rules
    const p = this.payments.get(a.reference);
    if (!p) return Promise.resolve("unknown");
    if (p.status === "success" || p.status === "needs_review") return Promise.resolve(p.status);
    if (a.amount !== p.expected || a.currency.toUpperCase() !== "NGN" || !p.user_id) {
      p.status = "needs_review";
      this.alerts.push({ kind: "amount_mismatch", reference: a.reference });
      return Promise.resolve("needs_review");
    }
    p.status = "success";
    this.memberships.push(a.reference);
    return Promise.resolve("success");
  }
  markAbandoned(reference: string) {
    const p = this.payments.get(reference);
    if (p?.status !== "pending") return Promise.resolve(false);
    p.status = "abandoned";
    return Promise.resolve(true);
  }
  payment(reference: string) {
    const p = this.payments.get(reference);
    return Promise.resolve(p ? { reference, user_id: p.user_id, status: p.status, created_at: p.created_at } : null);
  }
  stalePending(olderThan: Date) {
    return Promise.resolve([...this.payments].filter(([, p]) => p.status === "pending" && Date.parse(p.created_at) < olderThan.getTime())
      .map(([r]) => r));
  }
  insertEvent(e: { event: string; reference: string | null; raw: Record<string, unknown> }) { this.events.push(e); return Promise.resolve(); }
  alert(kind: string, reference: string | null) { this.alerts.push({ kind, reference }); return Promise.resolve(); }
  canDeleteAccount(user: string) { return Promise.resolve(![...this.payments.values()].some((p) => p.user_id === user && p.status === "pending")); }
  deleteUser(user: string) { this.deleted.push(user); return Promise.resolve(); }
}

/** Paystack as the API would answer, for the references in `txs`. */
function fakePaystack(txs: Record<string, Partial<Transaction>>, opts: { initFails?: boolean } = {}) {
  const calls: string[] = [];
  const ps = new Paystack(SECRET, (url: string, init?: RequestInit) => {
    calls.push(url);
    assertEquals((init?.headers as Record<string, string>).Authorization, `Bearer ${SECRET}`);
    if (url.endsWith("/transaction/initialize")) {
      return Promise.resolve(opts.initFails ? Response.json({ status: false, message: "down" }, { status: 503 })
        : Response.json({ status: true, data: { authorization_url: "https://checkout.paystack.com/x" } }));
    }
    const ref = decodeURIComponent(url.split("/transaction/verify/")[1]);
    const tx = txs[ref];
    return Promise.resolve(tx ? Response.json({ status: true, data: { reference: ref, id: 99, currency: "NGN", channel: "card", ...tx } })
      : Response.json({ status: false, message: "Transaction reference not found" }, { status: 400 }));
  });
  return { ps, calls };
}

const env = (over: Partial<{ secret: string; paymentsLive: boolean }> = {}) =>
  ({ secret: SECRET, paymentsLive: false, siteUrl: "https://site.test", ...over });

async function signed(body: unknown) {
  const raw = JSON.stringify(body);
  return { raw, sig: await sign(raw, SECRET) };
}

async function started(db: FakeDb, ps: Paystack, reference = "eo_1", user = "u1") {
  await payStart(db, ps, env(), { user, email: "m@test", plan: "monthly", siteMode: "test", reference });
}

Deno.test("pay-start: pending payment at the plan's price, then checkout", async () => {
  const db = new FakeDb();
  const { ps } = fakePaystack({});
  const r = await payStart(db, ps, env(), { user: "u1", email: "m@test", plan: "monthly", siteMode: "test" });
  assert(r.reference.startsWith("eo_") && r.url.startsWith("https://checkout.paystack.com"));
  assertEquals(db.payments.get(r.reference)?.status, "pending");
});

Deno.test("pay-start refuses live keys, mode mismatches and unknown plans", async () => {
  const db = new FakeDb();
  const { ps } = fakePaystack({});
  await assertRejects(() => payStart(db, ps, env({ secret: "sk_live_x" }), { user: "u", email: "e", plan: "monthly", siteMode: "test" }),
    HttpError, "live payments are not switched on");
  db.mode = "live";
  await assertRejects(() => payStart(db, ps, env({ secret: "sk_live_x" }), { user: "u", email: "e", plan: "monthly", siteMode: "live" }),
    HttpError, "live payments are not switched on");                          // live in the DB but PAYMENTS_LIVE unset
  await assertRejects(() => payStart(db, ps, env(), { user: "u", email: "e", plan: "monthly", siteMode: "live" }),
    HttpError, "payments mode mismatch");                                     // test key, live database
  db.mode = "test";
  await assertRejects(() => payStart(db, ps, env(), { user: "u", email: "e", plan: "monthly", siteMode: "live" }), HttpError);
  await assertRejects(() => payStart(db, ps, env(), { user: "u", email: "e", plan: "nope", siteMode: "test" }), HttpError);
  await assertRejects(() => payStart(db, ps, env({ secret: "" }), { user: "u", email: "e", plan: "monthly", siteMode: "test" }),
    HttpError, "not configured");
});

Deno.test("pay-start: a failed checkout start leaves the payment abandoned", async () => {
  const db = new FakeDb();
  const { ps } = fakePaystack({}, { initFails: true });
  await assertRejects(() => payStart(db, ps, env(), { user: "u", email: "e", plan: "monthly", siteMode: "test", reference: "eo_f" }),
    HttpError, "could not start checkout");
  assertEquals(db.payments.get("eo_f")?.status, "abandoned");
});

Deno.test("webhook: unsigned or tampered requests are refused", async () => {
  const db = new FakeDb();
  const { ps } = fakePaystack({});
  const { raw, sig } = await signed({ event: "charge.success", data: { reference: "eo_1" } });
  await assertRejects(() => webhook(db, ps, env(), raw, null), HttpError, "bad signature");
  await assertRejects(() => webhook(db, ps, env(), raw.replace("eo_1", "eo_2"), sig), HttpError, "bad signature");
  await assertRejects(() => webhook(db, ps, env(), raw, sig.replace(/^./, sig[0] === "a" ? "b" : "a")), HttpError);
  assertEquals(db.events.length, 0);
});

Deno.test("webhook: settles from Paystack's verified amount, not the webhook body; repeats change nothing", async () => {
  const db = new FakeDb();
  const { ps, calls } = fakePaystack({ eo_1: { status: "success", amount: 750000 }, eo_2: { status: "success", amount: 100000 } });
  await started(db, ps, "eo_1");
  await started(db, ps, "eo_2");
  const ok = await signed({ event: "charge.success", data: { reference: "eo_1", amount: 1 } });
  assertEquals((await webhook(db, ps, env(), ok.raw, ok.sig)).result, "success");
  assert(calls.some((c) => c.endsWith("/transaction/verify/eo_1")));
  assertEquals((await webhook(db, ps, env(), ok.raw, ok.sig)).result, "success");
  assertEquals(db.memberships, ["eo_1"]);
  const short = await signed({ event: "charge.success", data: { reference: "eo_2", amount: 750000 } });
  assertEquals((await webhook(db, ps, env(), short.raw, short.sig)).result, "needs_review");
  assertEquals(db.alerts.map((a) => a.kind), ["amount_mismatch"]);
});

Deno.test("webhook: a late success after reconcile gave up still settles", async () => {
  const db = new FakeDb();
  const { ps } = fakePaystack({ eo_1: { status: "success", amount: 750000 } });
  await started(db, ps, "eo_1");
  await db.markAbandoned("eo_1");
  const { raw, sig } = await signed({ event: "charge.success", data: { reference: "eo_1" } });
  assertEquals((await webhook(db, ps, env(), raw, sig)).result, "success");
  assertEquals(db.memberships, ["eo_1"]);
});

Deno.test("webhook: refunds and disputes raise alerts; events are stored stripped", async () => {
  const db = new FakeDb();
  const { ps } = fakePaystack({});
  const refund = await signed({ event: "refund.processed", data: { amount: 750000, status: "processed",
    transaction: { reference: "eo_1", customer: { email: "m@test" }, authorization: { last4: "4081" }, ip_address: "1.2.3.4" } } });
  const dispute = await signed({ event: "charge.dispute.create", data: { reason: "fraud", transaction: { reference: "eo_1" } } });
  assertEquals((await webhook(db, ps, env(), refund.raw, refund.sig)).result, "alerted");
  assertEquals((await webhook(db, ps, env(), dispute.raw, dispute.sig)).result, "alerted");
  assertEquals(db.alerts, [{ kind: "refund", reference: "eo_1" }, { kind: "dispute", reference: "eo_1" }]);
  const stored = JSON.stringify(db.events);
  assert(!/m@test|4081|1\.2\.3\.4|customer|authorization|ip_address/.test(stored), stored);
});

Deno.test("strip keeps accounting fields only", () => {
  assertEquals(Object.keys(strip({ id: 1, reference: "r", customer: {}, authorization: {}, ip_address: "x", amount: 5 })).sort(),
    ["amount", "channel", "currency", "fees", "gateway_response", "id", "paid_at", "reference", "status"]);
});

Deno.test("pay-verify: only the payment's owner", async () => {
  const db = new FakeDb();
  const { ps } = fakePaystack({ eo_1: { status: "success", amount: 750000 } });
  await started(db, ps, "eo_1", "u1");
  await assertRejects(() => payVerify(db, ps, env(), { user: "u2", reference: "eo_1" }), HttpError, "payment not found");
  assertEquals(await payVerify(db, ps, env(), { user: "u1", reference: "eo_1" }), { status: "success" });
});

Deno.test("reconcile: stale payments settle, give up, or wait", async () => {
  const db = new FakeDb();
  const { ps } = fakePaystack({ eo_paid: { status: "success", amount: 750000 }, eo_gone: { status: "abandoned", amount: 750000 },
                               eo_slow: { status: "ongoing", amount: 750000 } });
  for (const r of ["eo_paid", "eo_gone", "eo_slow", "eo_unknown", "eo_new"]) await started(db, ps, r);
  for (const r of ["eo_paid", "eo_gone", "eo_slow", "eo_unknown"]) db.payments.get(r)!.created_at = new Date(Date.now() - 30 * 3600_000).toISOString();
  const out = await reconcile(db, ps, env());
  assertEquals(out, { eo_paid: "success", eo_gone: "abandoned", eo_slow: "ongoing", eo_unknown: "abandoned" });
  assertEquals(db.payments.get("eo_new")?.status, "pending");                // under 24 hours: untouched
});

Deno.test("delete-account: recent sign-in (amr), no pending payment", async () => {
  const db = new FakeDb();
  const { ps } = fakePaystack({});
  const now = new Date("2026-10-26T12:00:00Z");
  const t = now.getTime() / 1000;
  await assertRejects(() => deleteAccount(db, { user: "u1", signedInAt: t - 3600, now }), HttpError, "sign in again");
  await assertRejects(() => deleteAccount(db, { user: "u1", signedInAt: null, now }), HttpError, "sign in again");
  await started(db, ps, "eo_1", "u1");
  await assertRejects(() => deleteAccount(db, { user: "u1", signedInAt: t - 60, now }), HttpError, "pending");
  await db.markAbandoned("eo_1");
  assertEquals(await deleteAccount(db, { user: "u1", signedInAt: t - 60, now }), { deleted: true });
  assertEquals(db.deleted, ["u1"]);
});
