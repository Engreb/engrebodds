import type { APIRoute } from "astro";
import { SITE_INDEXING } from "astro:env/server";

// Until launch (SITE_INDEXING=off) every crawler is turned away; afterwards, everything but the OG images.
export const GET: APIRoute = ({ site, url }) => {
  const body = SITE_INDEXING === "on"
    ? `User-agent: *\nAllow: /\nDisallow: /og/\nSitemap: ${new URL("/sitemap.xml", site ?? url.origin)}\n`
    : "User-agent: *\nDisallow: /\n";
  return new Response(body, { headers: { "Content-Type": "text/plain; charset=utf-8" } });
};
