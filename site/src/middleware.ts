import type { APIContext, MiddlewareNext } from "astro";
import { SITE_INDEXING } from "astro:env/server";
import { defineMiddleware } from "astro:middleware";
import { requestClient, verifiedUser } from "./lib/auth";
import { decide, hasSession, PRIVATE_HEADERS } from "./lib/cache";
import { publicClient } from "./lib/db";
import { previewOn } from "./lib/source";

export const onRequest = defineMiddleware(async (context, next) => {
  const response = await handle(context, next);
  if (SITE_INDEXING === "on") return response;
  try {
    response.headers.set("X-Robots-Tag", "noindex, nofollow");
    return response;
  } catch { // immutable headers (e.g. a redirect): copy the response
    const copy = new Response(response.body, response);
    copy.headers.set("X-Robots-Tag", "noindex, nofollow");
    return copy;
  }
});

async function handle(context: APIContext, next: MiddlewareNext): Promise<Response> {
  // Who is asking: a session cookie is only believed after getClaims() verifies it.
  context.locals.user = null;
  context.locals.supabase = publicClient();
  if (hasSession(context.request.headers.get("cookie"))) {
    const client = requestClient(context);
    const user = await verifiedUser(client);
    if (user) {
      context.locals.supabase = client;
      context.locals.user = user;
    }
  }

  const decision = decide(context.request, { preview: previewOn() });
  if (decision.kind === "redirect") return context.redirect(decision.location, 301);
  if (decision.kind === "cache") {
    context.cache.set({ maxAge: decision.maxAge, swr: decision.swr });
    const response = await next();
    // A response that sets a cookie is personal: never let the edge keep it.
    return [...context.cookies.headers()].length ? privatise(response) : response;
  }
  context.cache.set(false);
  return privatise(await next());
}

function privatise(response: Response): Response {
  for (const [k, v] of Object.entries(PRIVATE_HEADERS)) response.headers.set(k, v);
  response.headers.delete("Cloudflare-CDN-Cache-Control");
  response.headers.delete("Cache-Tag");
  return response;
}
