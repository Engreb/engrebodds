// The Cloudflare runtime module, as much of it as the site uses (avoids pulling in all of workers-types).
declare module "cloudflare:workers" {
  export const env: Record<string, unknown>;
}
