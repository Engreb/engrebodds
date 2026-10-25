import type { APIRoute } from "astro";
import { loadDay } from "../../lib/db";
import { renderCard } from "../../lib/og";
import { bankerCard } from "../../lib/og-cards";
import { banker } from "../../lib/site";

// Always the public view (never preview data): the image may be cached and shared.
export const GET: APIRoute = async (ctx) => {
  const { fixture, tip } = banker(await loadDay());
  return renderCard(ctx, bankerCard(fixture, tip));
};
