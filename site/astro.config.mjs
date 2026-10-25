// @ts-check
import cloudflare from "@astrojs/cloudflare";
import { cacheCloudflare } from "@astrojs/cloudflare/cache";
import { defineConfig, envField } from "astro/config";
import { loadEnv } from "vite";

// `--mode test` runs against the local database: select wrangler's "test" environment.
const argv = process.argv;
if (argv[argv.indexOf("--mode") + 1] === "test" && argv.includes("--mode")) process.env.CLOUDFLARE_ENV = "test";

// Preview mode reads unpublished tips with a service key. It exists for local dev only:
// refuse to build a deployable bundle while it is switched on.
const fileEnv = loadEnv("production", process.cwd(), "");
/** @param {unknown} v */
const on = (v) => ["1", "true"].includes(String(v ?? "").trim().toLowerCase());
if (process.argv.includes("build") && (on(process.env.SITE_PREVIEW) || on(fileEnv.SITE_PREVIEW))) {
  throw new Error("SITE_PREVIEW is set: preview mode is local-only. Unset it before building.");
}

export default defineConfig({
  site: "https://engrebodds.com",
  output: "server",
  adapter: cloudflare({ imageService: "passthrough" }),
  session: false, // no server sessions; Phase 5 uses Supabase Auth cookies
  trailingSlash: "never",
  cache: { provider: cacheCloudflare() },
  env: {
    schema: {
      SUPABASE_URL: envField.string({ context: "server", access: "public" }),
      SUPABASE_KEY: envField.string({ context: "server", access: "public" }),
      SITE_PREVIEW: envField.boolean({ context: "server", access: "public", default: false }),
      SUPABASE_SERVICE_KEY: envField.string({ context: "server", access: "secret", optional: true }),
    },
  },
});
