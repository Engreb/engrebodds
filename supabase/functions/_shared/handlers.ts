// The payment logic of every Edge Function, with the database and Paystack passed in (so tests can fake them).
// Money rules live in the database functions (record_payment etc.); these handlers verify, log and route.

import { keyMode, type Mode, type Paystack, strip, validSignature } from "./paystack.ts";

export interface PaymentRow { reference: string; user_id: string | null; status: string; created_at: string }

export interface Db {
  paymentsMode(): Promise<Mode>;
  startPayment(user: string, plan: string, reference: string, siteMode: string): Promise<{ amount_kobo: number; currency: string; mode: Mode }>;
  recordPayment(a: { reference: string; paystack_id: number; amount: number; currency: string; channel: string | null;
                     paid_at: string | null; raw: Record<string, unknown> }): Promise<string>;
  markAbandoned(reference: string): Promise<boolean>;
  payment(reference: string): Promise<PaymentRow | null>;
  stalePending(olderThan: Date): Promise<string[]>;
  insertEvent(e: { event: string; reference: string | null; raw: Record<string, unknown> }): Promise<void>;
  alert(kind: string, reference: string | null, detail: Record<string, unknown>): Promise<void>;
  canDeleteAccount(user: string): Promise<boolean>;
  deleteUser(user: string): Promise<void>;
}

export interface Env { secret: string | undefined; paymentsLive: boolean; siteUrl: string }

export class HttpError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

/** The Paystack key's mode must agree with the database (and live needs PAYMENTS_LIVE=yes as well). */
export async function checkKey(db: Db, env: Env): Promise<Mode> {
  const keyM = keyMode(env.secret);
  if (!keyM) throw new HttpError(503, "payments are not configured");
  const dbM = await db.paymentsMode();
  if (keyM === "live" && !(dbM === "live" && env.paymentsLive)) throw new HttpError(503, "live payments are not switched on");
  if (keyM !== dbM) throw new HttpError(503, "payments mode mismatch");
  return dbM;
}

// ---------- pay-start ----------
export async function payStart(db: Db, ps: Paystack, env: Env,
                               a: { user: string; email: string; plan: string; siteMode: string; reference?: string }) {
  await checkKey(db, env);
  const reference = a.reference ?? `eo_${crypto.randomUUID()}`;
  let started;
  try {
    started = await db.startPayment(a.user, a.plan, reference, a.siteMode);
  } catch (e) {
    throw new HttpError(400, (e as Error).message);
  }
  try {
    const url = await ps.initialize({
      email: a.email, amount: started.amount_kobo, currency: started.currency, reference,
      callback_url: `${env.siteUrl}/account`, metadata: { plan: a.plan, mode: started.mode },
    });
    return { reference, url };
  } catch (e) {
    await db.markAbandoned(reference);
    throw new HttpError(502, `could not start checkout: ${(e as Error).message}`);
  }
}

/** Ask Paystack, then let the database settle it. Amount and currency come from Paystack's verify, not the caller. */
async function settle(db: Db, ps: Paystack, reference: string): Promise<string> {
  const tx = await ps.verify(reference);
  if (!tx) return "unknown";
  if (tx.reference !== reference) throw new HttpError(502, "Paystack returned a different reference");
  if (tx.status !== "success") return tx.status;
  return db.recordPayment({
    reference, paystack_id: tx.id, amount: tx.amount, currency: tx.currency, channel: tx.channel ?? null,
    paid_at: tx.paid_at ?? null, raw: strip(tx as unknown as Record<string, unknown>),
  });
}

// ---------- paystack-webhook ----------
export async function webhook(db: Db, ps: Paystack, env: Env, rawBody: string, signature: string | null) {
  if (!env.secret || !(await validSignature(rawBody, signature, env.secret))) throw new HttpError(401, "bad signature");
  const body = JSON.parse(rawBody) as { event: string; data: Record<string, unknown> };
  const data = body.data ?? {};
  const tx = (data.transaction ?? data) as Record<string, unknown>;      // refunds and disputes wrap the transaction
  const reference = (tx.reference ?? data.reference ?? data.transaction_reference ?? null) as string | null;
  await db.insertEvent({ event: body.event, reference, raw: strip(tx) });

  switch (body.event) {
    case "charge.success":
      if (!reference) throw new HttpError(400, "no reference");
      await checkKey(db, env);
      return { event: body.event, result: await settle(db, ps, reference) };
    case "refund.processed":
      await db.alert("refund", reference, { amount: data.amount ?? null, status: data.status ?? null });
      return { event: body.event, result: "alerted" };
    case "charge.dispute.create":
      await db.alert("dispute", reference, { reason: data.reason ?? null, category: data.category ?? null });
      return { event: body.event, result: "alerted" };
    default:
      return { event: body.event, result: "logged" };
  }
}

// ---------- pay-verify (the member's own reference, on return from checkout) ----------
export async function payVerify(db: Db, ps: Paystack, env: Env, a: { user: string; reference: string }) {
  const p = await db.payment(a.reference);
  if (!p || p.user_id !== a.user) throw new HttpError(404, "payment not found");
  if (p.status === "success" || p.status === "needs_review") return { status: p.status };
  await checkKey(db, env);
  const result = await settle(db, ps, a.reference);
  return { status: result === "success" || result === "needs_review" ? result : p.status };
}

// ---------- pay-reconcile (daily) ----------
const GIVE_UP = new Set(["abandoned", "failed", "reversed", "unknown"]);

export async function reconcile(db: Db, ps: Paystack, env: Env, now = new Date()) {
  await checkKey(db, env);
  const out: Record<string, string> = {};
  for (const reference of await db.stalePending(new Date(now.getTime() - 24 * 3600_000))) {
    try {
      const result = await settle(db, ps, reference);
      if (GIVE_UP.has(result)) {
        await db.markAbandoned(reference);
        out[reference] = "abandoned";
      } else {
        out[reference] = result;                       // success, needs_review, or still in progress at Paystack
      }
    } catch (e) {
      out[reference] = `error: ${(e as Error).message}`;
    }
  }
  return out;
}

// ---------- delete-account ----------
export const REAUTH_SECONDS = 600;

export async function deleteAccount(db: Db, a: { user: string; signedInAt: number | null; now?: Date }) {
  const now = Math.floor((a.now ?? new Date()).getTime() / 1000);
  if (!a.signedInAt || now - a.signedInAt > REAUTH_SECONDS) throw new HttpError(403, "sign in again to delete your account");
  if (!(await db.canDeleteAccount(a.user))) throw new HttpError(409, "a payment is still pending; finish it or wait for it to expire");
  await db.deleteUser(a.user);
  return { deleted: true };
}
