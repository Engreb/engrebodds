// POST { plan, siteMode } by a signed-in user: a pending payment at the plan's price, then Paystack checkout.
import { payStart } from "../_shared/handlers.ts";
import { Paystack } from "../_shared/paystack.ts";
import { admin, paystackBase, caller, env, respond, supabaseDb } from "../_shared/server.ts";

Deno.serve((req) => respond(async () => {
  const sb = admin();
  const user = await caller(req, sb);
  const { plan, siteMode } = await req.json();
  const e = env();
  return payStart(supabaseDb(sb), new Paystack(e.secret ?? "", fetch, paystackBase()), e, { user: user.id, email: user.email, plan, siteMode });
}));
