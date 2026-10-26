// Signed-in visitors (Phase 5). The session lives in Supabase's own cookies; identity is always
// established with getClaims() (signature-checked, falling back to the Auth server), never getSession().
import { createServerClient, parseCookieHeader } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";
import type { APIContext, AstroCookieSetOptions } from "astro";
import { SUPABASE_KEY, SUPABASE_URL } from "astro:env/server";

export interface SessionUser {
  id: string;
  email: string | null;
  signedInAt: number | null; // seconds, from the amr claim (not iat, which refreshes renew)
}

export function requestClient(ctx: Pick<APIContext, "request" | "cookies">): SupabaseClient {
  return createServerClient(SUPABASE_URL, SUPABASE_KEY, {
    cookies: {
      getAll: () => parseCookieHeader(ctx.request.headers.get("cookie") ?? "").map((c) => ({ name: c.name, value: c.value ?? "" })),
      setAll: (cookies) => {
        for (const { name, value, options } of cookies) {
          ctx.cookies.set(name, value, { ...options, path: options.path ?? "/" } as AstroCookieSetOptions);
        }
      },
    },
  });
}

interface Claims { sub?: string; email?: string; amr?: { method: string; timestamp: number }[] }

/** The verified user behind the request's session cookie, or null. */
export async function verifiedUser(client: SupabaseClient): Promise<SessionUser | null> {
  const { data, error } = await client.auth.getClaims();
  const claims = data?.claims as Claims | undefined;
  if (error || !claims?.sub) return null;
  const signIns = (claims.amr ?? []).map((a) => a.timestamp).filter((t) => typeof t === "number");
  return { id: claims.sub, email: claims.email ?? null, signedInAt: signIns.length ? Math.max(...signIns) : null };
}

/** A relative path to return to after signing in (never another site). */
export function safeNext(next: string | null | undefined, fallback = "/account"): string {
  return next && /^\/(?!\/)[\w\-/.?=&%]*$/.test(next) ? next : fallback;
}
