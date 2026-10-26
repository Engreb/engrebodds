// POST { reference } by its signed-in owner, on return from checkout: settle now rather than wait for the webhook.
import { payVerify } from "../_shared/handlers.ts";
import { Paystack } from "../_shared/paystack.ts";
import { admin, paystackBase, caller, env, respond, supabaseDb } from "../_shared/server.ts";

Deno.serve((req) => respond(async () => {
  const sb = admin();
  const user = await caller(req, sb);
  const { reference } = await req.json();
  const e = env();
  return payVerify(supabaseDb(sb), new Paystack(e.secret ?? "", fetch, paystackBase()), e, { user: user.id, reference: String(reference ?? "") });
}));
