// Daily (Ingest workflow): pending payments older than 24h are settled from Paystack or marked abandoned.
// Called with RECONCILE_TOKEN, never the service key.
import { HttpError, reconcile } from "../_shared/handlers.ts";
import { Paystack } from "../_shared/paystack.ts";
import { admin, paystackBase, env, respond, supabaseDb } from "../_shared/server.ts";

Deno.serve((req) => respond(async () => {
  const token = Deno.env.get("RECONCILE_TOKEN");
  if (!token || req.headers.get("x-reconcile-token") !== token) throw new HttpError(401, "bad token");
  const e = env();
  return reconcile(supabaseDb(admin()), new Paystack(e.secret ?? "", fetch, paystackBase()), e);
}));
