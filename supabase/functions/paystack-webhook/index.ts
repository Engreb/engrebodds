// Paystack's webhook (no Supabase JWT: verified by its HMAC-SHA512 signature instead).
import { webhook } from "../_shared/handlers.ts";
import { Paystack } from "../_shared/paystack.ts";
import { admin, paystackBase, env, respond, supabaseDb } from "../_shared/server.ts";

Deno.serve(async (req) => {
  const raw = await req.text();
  const e = env();
  return respond(() => webhook(supabaseDb(admin()), new Paystack(e.secret ?? "", fetch, paystackBase()), e, raw, req.headers.get("x-paystack-signature")));
});
