// Where a page's data comes from: the public database, or (local dev with SITE_PREVIEW) the preview.
import type { AstroGlobal } from "astro";
import { SITE_PREVIEW, SUPABASE_SERVICE_KEY } from "astro:env/server";
import { loadDay } from "./db";
import type { Day } from "./types";

export const PREVIEW_MEMBER_COOKIE = "eo_preview_member";
/** Local dev only, and only once the secret key is in .env.development.local. */
export const previewOn = () => import.meta.env.DEV && SITE_PREVIEW && Boolean(SUPABASE_SERVICE_KEY);

export async function getDay(Astro: AstroGlobal, date?: string): Promise<Day> {
  if (import.meta.env.DEV && previewOn()) {
    const { loadPreviewDay } = await import("./preview");
    return loadPreviewDay(date, Astro.cookies.get(PREVIEW_MEMBER_COOKIE)?.value === "1");
  }
  return loadDay(date);
}
