// POST by a signed-in user who signed in within the last 10 minutes (amr claim): deletes the account.
// Payments and memberships are kept with a null user; refused while a payment is pending.
import { deleteAccount } from "../_shared/handlers.ts";
import { admin, caller, respond, supabaseDb } from "../_shared/server.ts";

Deno.serve((req) => respond(async () => {
  const sb = admin();
  const user = await caller(req, sb);
  return deleteAccount(supabaseDb(sb), { user: user.id, signedInAt: user.signedInAt });
}));
