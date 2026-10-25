// Edge-cache policy (pure, unit-tested). Anonymous pages are shared and cached briefly; anything that
// could be personal (Phase 5 members) is never cached.
//
// Phase 5 also needs a Cloudflare Cache Rule, because the Worker cache answers before our code runs:
// "bypass cache when the request has cookie eo_session or an Authorization header" (deploy checklist).

export const SESSION_COOKIE = "eo_session";
export const MAX_AGE = 60;
export const SWR = 600;
/** Query parameters that change a page. Any other parameter is dropped with a redirect, so it can't split the cache. */
export const QUERY_ALLOWLIST = new Set(["league"]);

export type Decision =
  | { kind: "cache"; maxAge: number; swr: number }
  | { kind: "private" }
  | { kind: "redirect"; location: string };

export function hasSession(cookieHeader: string | null): boolean {
  if (!cookieHeader) return false;
  return cookieHeader.split(";").some((c) => c.trim().startsWith(`${SESSION_COOKIE}=`));
}

export function decide(request: Request, opts: { preview?: boolean } = {}): Decision {
  const url = new URL(request.url);
  const personal = hasSession(request.headers.get("cookie")) || request.headers.has("authorization");
  if (opts.preview || personal || !["GET", "HEAD"].includes(request.method)) return { kind: "private" };

  const kept = [...url.searchParams.keys()].filter((k) => QUERY_ALLOWLIST.has(k));
  if (kept.length !== [...url.searchParams.keys()].length) {
    const clean = new URLSearchParams();
    for (const k of kept) clean.set(k, url.searchParams.get(k)!);
    const q = clean.toString();
    return { kind: "redirect", location: url.pathname + (q ? `?${q}` : "") };
  }
  return { kind: "cache", maxAge: MAX_AGE, swr: SWR };
}

export const PRIVATE_HEADERS: Record<string, string> = { "Cache-Control": "private, no-store", Vary: "Cookie" };
export const publicHeader = (maxAge = MAX_AGE, swr = SWR) => `public, s-maxage=${maxAge}, stale-while-revalidate=${swr}`;
