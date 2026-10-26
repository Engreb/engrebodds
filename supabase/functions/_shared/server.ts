// Wiring for the deployed Edge Functions: the service-role database, the caller's verified identity, env.
import { createClient, type SupabaseClient } from "npm:@supabase/supabase-js@2.118.0";
import type { Db, Env, PaymentRow } from "./handlers.ts";
import { HttpError } from "./handlers.ts";
import type { Mode } from "./paystack.ts";

export function admin(): SupabaseClient {
  return createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}

/** Paystack's API, or the local fake (site/tests/fake-paystack.mjs) when PAYSTACK_BASE is set in the local env file. */
export const paystackBase = () => Deno.env.get("PAYSTACK_BASE") || "https://api.paystack.co";

export function env(): Env {
  return {
    secret: Deno.env.get("PAYSTACK_SECRET_KEY") || undefined,
    paymentsLive: Deno.env.get("PAYMENTS_LIVE") === "yes",
    siteUrl: (Deno.env.get("SITE_URL") ?? "http://localhost:4321").replace(/\/$/, ""),
  };
}

function must<T>(res: { data: T | null; error: { message: string } | null }, what: string): T {
  if (res.error) throw new Error(`${what}: ${res.error.message}`);
  return res.data as T;
}

export function supabaseDb(sb: SupabaseClient): Db {
  return {
    paymentsMode: async () => must(await sb.rpc("payments_mode"), "payments_mode") as Mode,
    startPayment: async (user, plan, reference, siteMode) => {
      const rows = must(await sb.rpc("start_payment", { p_user: user, p_plan: plan, p_reference: reference, p_site_mode: siteMode }),
                        "start_payment") as { amount_kobo: number; currency: string; mode: Mode }[];
      return rows[0];
    },
    recordPayment: async (a) => must(await sb.rpc("record_payment", {
      p_reference: a.reference, p_paystack_id: a.paystack_id, p_amount: a.amount, p_currency: a.currency,
      p_channel: a.channel, p_paid_at: a.paid_at, p_raw: a.raw,
    }), "record_payment") as string,
    markAbandoned: async (reference) => must(await sb.rpc("mark_abandoned", { p_reference: reference }), "mark_abandoned") as boolean,
    payment: async (reference) => must(await sb.from("payments").select("reference, user_id, status, created_at")
      .eq("reference", reference).maybeSingle(), "payment") as PaymentRow | null,
    stalePending: async (olderThan) => (must(await sb.from("payments").select("reference").eq("status", "pending")
      .lt("created_at", olderThan.toISOString()).limit(200), "stale") as { reference: string }[]).map((r) => r.reference),
    insertEvent: async (e) => {
      const p = e.reference ? (must(await sb.from("payments").select("id").eq("reference", e.reference).maybeSingle(), "payment") as { id: number } | null) : null;
      must(await sb.from("payment_events").insert({ event: e.event, reference: e.reference, payment_id: p?.id ?? null, raw: e.raw }), "event");
    },
    alert: async (kind, reference, detail) => {
      const p = reference ? (must(await sb.from("payments").select("id").eq("reference", reference).maybeSingle(), "payment") as { id: number } | null) : null;
      must(await sb.from("admin_alerts").insert({ kind, payment_id: p?.id ?? null, detail: { reference, ...detail } }), "alert");
    },
    canDeleteAccount: async (user) => must(await sb.rpc("can_delete_account", { p_user: user }), "can_delete_account") as boolean,
    deleteUser: async (user) => {
      const { error } = await sb.auth.admin.deleteUser(user);
      if (error) throw new Error(`delete user: ${error.message}`);
    },
  };
}

export interface Caller { id: string; email: string; signedInAt: number | null }

/** The signed-in caller, verified with getClaims (signature-checked), from the Authorization header. */
export async function caller(req: Request, sb: SupabaseClient): Promise<Caller> {
  const jwt = (req.headers.get("authorization") ?? "").replace(/^Bearer\s+/i, "");
  if (!jwt) throw new HttpError(401, "sign in first");
  const { data, error } = await sb.auth.getClaims(jwt);
  const c = data?.claims as { sub?: string; email?: string; role?: string; amr?: { timestamp: number }[] } | undefined;
  if (error || !c?.sub || c.role !== "authenticated") throw new HttpError(401, "sign in first");
  const times = (c.amr ?? []).map((a) => a.timestamp).filter((t) => typeof t === "number");
  return { id: c.sub, email: c.email ?? "", signedInAt: times.length ? Math.max(...times) : null };
}

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

export async function respond(fn: () => Promise<unknown>): Promise<Response> {
  try {
    return json(await fn());
  } catch (e) {
    if (e instanceof HttpError) return json({ error: e.message }, e.status);
    console.error(e);
    return json({ error: "internal error" }, 500);
  }
}
