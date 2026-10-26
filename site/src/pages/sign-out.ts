import type { APIRoute } from "astro";
import { requestClient } from "../lib/auth";

// POST only (a form button), so a link or prefetch can't sign anyone out.
export const POST: APIRoute = async (ctx) => {
  await requestClient(ctx).auth.signOut({ scope: "local" });
  return ctx.redirect("/", 303);
};
