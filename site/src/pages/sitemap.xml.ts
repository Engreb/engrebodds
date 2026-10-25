import type { APIRoute } from "astro";
import { loadDay } from "../lib/db";
import { shiftDate } from "../lib/day";
import { BOOKMAKERS } from "../lib/site";

const STATIC = ["/", "/predictions", "/banker-of-the-day", "/2-odds-today", "/5-odds-today", "/10-odds-today", "/sure-odds-today",
  "/gg-ng", "/over-1-5", "/over-under-2-5", "/double-chance", "/correct-score", "/bet-builder", "/leagues", "/track-record",
  "/pricing", "/how-our-predictions-work", "/responsible-gambling", "/about"];

// Today's and tomorrow's match and league pages, plus the fixed pages. Rebuilt at most once a day.
export const GET: APIRoute = async (ctx) => {
  const today = await loadDay();
  const tomorrow = await loadDay(shiftDate(today.window.date, 1));
  const fixtures = [...today.fixtures, ...tomorrow.fixtures];
  const urls = [
    ...STATIC,
    ...BOOKMAKERS.map((b) => `/bookmakers/${b.slug}`),
    ...[...new Set(fixtures.map((f) => `/leagues/${f.league.slug}`))],
    ...fixtures.map((f) => `/predictions/${f.slug}`),
  ];
  const site = ctx.site ?? new URL(ctx.url.origin);
  const xml = `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n` +
    urls.map((u) => `  <url><loc>${new URL(u, site).toString().replace(/&/g, "&amp;")}</loc></url>`).join("\n") + "\n</urlset>\n";
  ctx.cache.set({ maxAge: 86_400, swr: 3600 });
  return new Response(xml, { headers: { "Content-Type": "application/xml; charset=utf-8" } });
};
