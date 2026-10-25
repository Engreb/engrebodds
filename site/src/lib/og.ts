// Open Graph cards (1200x630 PNG) for sharing on WhatsApp and social media. They carry only what the
// page shows a visitor: a locked pick is never drawn.

import type { APIContext } from "astro";

export interface Card {
  eyebrow: string;
  title: string;
  lines: string[];
  big?: string; // a price or headline number, drawn in mono
}

const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);

export function cardHtml(c: Card): string {
  return `<div style="display:flex;flex-direction:column;justify-content:space-between;align-items:stretch;width:100%;height:100%;padding:64px 72px;background:#0f3d2e;color:#eaf5ef;font-family:Barlow">
  <div style="display:flex;align-items:baseline;font-size:44px;font-weight:800">Engreb<span style="color:#fb7c3c">odds</span></div>
  <div style="display:flex;flex-direction:column">
    <div style="display:flex;font-size:30px;font-weight:600;letter-spacing:2px;color:#fb7c3c;text-transform:uppercase">${esc(c.eyebrow)}</div>
    <div style="display:flex;font-size:76px;font-weight:800;line-height:1.02;margin-top:12px">${esc(c.title)}</div>
    ${c.lines.map((l) => `<div style="display:flex;font-size:34px;font-weight:600;margin-top:10px;color:#cfe3d8">${esc(l)}</div>`).join("")}
  </div>
  <div style="display:flex;width:100%;justify-content:space-between;align-items:flex-end">
    <div style="display:flex;font-size:24px;font-weight:600;color:#9fbcae">18+ · Predictions are estimates, never guarantees</div>
    ${c.big ? `<div style="display:flex;font-family:Plex;font-size:72px;font-weight:600;color:#fb7c3c">${esc(c.big)}</div>` : ""}
  </div>
</div>`;
}

async function asset(ctx: APIContext, path: string): Promise<ArrayBuffer> {
  const url = new URL(path, ctx.url);
  try {
    // On Cloudflare, read static files through the assets binding (a fetch to our own hostname wouldn't reach them).
    const { env } = (await import("cloudflare:workers")) as { env: { ASSETS?: { fetch: (u: URL) => Promise<Response> } } };
    if (env.ASSETS) return (await env.ASSETS.fetch(url)).arrayBuffer();
  } catch { /* dev without the binding */ }
  return (await fetch(url)).arrayBuffer();
}

export async function renderCard(ctx: APIContext, card: Card): Promise<Response> {
  const { ImageResponse, CustomFont, cache } = await import("@cf-wasm/og/workerd");
  const { t } = await import("@cf-wasm/og/html-to-react");
  const cf = (ctx.locals as { cfContext?: { waitUntil(p: Promise<unknown>): void } }).cfContext;
  if (cf) cache.setExecutionContext(cf);
  const fonts = [
    new CustomFont("Barlow", () => asset(ctx, "/fonts/og/barlow-condensed-600.woff"), { weight: 600 }),
    new CustomFont("Barlow", () => asset(ctx, "/fonts/og/barlow-condensed-800.woff"), { weight: 800 }),
    new CustomFont("Plex", () => asset(ctx, "/fonts/og/ibm-plex-mono-600.woff"), { weight: 600 }),
  ];
  // Whitespace between tags would become flex items and push the footer around.
  const html = cardHtml(card).replace(/>\s+</g, "><");
  const res = await ImageResponse.async(t(html), { width: 1200, height: 630, fonts });
  const out = new Response(res.body, res);
  ctx.cache.set({ maxAge: 3600, swr: 86_400 });
  return out;
}
