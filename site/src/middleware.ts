import { defineMiddleware } from "astro:middleware";
import { decide, PRIVATE_HEADERS } from "./lib/cache";
import { previewOn } from "./lib/source";

export const onRequest = defineMiddleware(async (context, next) => {
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
});
