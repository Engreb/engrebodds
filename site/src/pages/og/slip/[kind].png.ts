import type { APIRoute } from "astro";
import { loadDay } from "../../../lib/db";
import { renderCard } from "../../../lib/og";
import { SLIP_TITLES, slipCard } from "../../../lib/og-cards";
import { slipOf } from "../../../lib/site";

export const GET: APIRoute = async (ctx) => {
  const kind = ctx.params.kind ?? "";
  if (!SLIP_TITLES[kind]) return new Response(null, { status: 404 });
  return renderCard(ctx, slipCard(kind, slipOf(await loadDay(), kind)));
};
