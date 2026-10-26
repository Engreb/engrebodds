// The Cloudflare runtime module, as much of it as the site uses (avoids pulling in all of workers-types).
declare module "cloudflare:workers" {
  export const env: Record<string, unknown>;
}

declare namespace App {
  interface Locals {
    /** The request's database client: the visitor's own session when signed in, else the public one. */
    supabase: import("@supabase/supabase-js").SupabaseClient;
    /** The verified user (getClaims), or null. */
    user: import("./lib/auth").SessionUser | null;
  }
}
