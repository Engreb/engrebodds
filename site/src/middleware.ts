import type { APIContext, MiddlewareNext } from "astro";
import { SITE_INDEXING } from "astro:env/server";
import { defineMiddleware } from "astro:middleware";
import { decide, PRIVATE_HEADERS } from "./lib/cache";
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
  const decision = decide(context.request, { preview: previewOn() });
  if (decision.kind === "redirect") return context.redirect(decision.location, 301);
  if (decision.kind === "cache") {
    context.cache.set({ maxAge: decision.maxAge, swr: decision.swr });
    return next();
  }
  context.cache.set(false);
  const response = await next();
  for (const [k, v] of Object.entries(PRIVATE_HEADERS)) response.headers.set(k, v);
  return response;
}
