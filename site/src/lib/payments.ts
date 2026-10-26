// Calls to the payment Edge Functions, as the signed-in user (the client carries their session).
import type { SupabaseClient } from "@supabase/supabase-js";
import { PAYMENTS } from "astro:env/server";

export interface Invoked<T> { data?: T; error?: string; status?: number }

export async function invoke<T>(db: SupabaseClient, name: string, body: Record<string, unknown> = {}): Promise<Invoked<T>> {
  const { data, error } = await db.functions.invoke(name, { body });
  if (!error) return { data: data as T };
  const ctx = (error as { context?: Response }).context;
  let message = "Something went wrong. Please try again.";
  if (ctx && typeof ctx.json === "function") {
    try { message = ((await ctx.json()) as { error?: string }).error ?? message; } catch { /* not JSON */ }
  }
  return { error: message, status: ctx?.status };
}

/** Checkout is only offered when the site's PAYMENTS switch and the database's payments_mode agree. */
export async function modeReady(db: SupabaseClient): Promise<boolean> {
  if (PAYMENTS === "off") return false;
  const { data, error } = await db.rpc("payments_mode");
  return !error && data === PAYMENTS;
}
