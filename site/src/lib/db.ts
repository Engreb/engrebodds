// Reads for the site, with the public key only: row-level security decides what comes back,
// so locked tips and predictions never reach this code, let alone the browser.

import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { SUPABASE_KEY, SUPABASE_URL } from "astro:env/server";
import { publishingWindow, windowFor } from "./day";
import { coveredFixtures, daySlips, groupTips, visiblePredictions, visibleTips } from "./queries";
import type { Day } from "./types";

let anon: SupabaseClient | undefined;
export function publicClient(): SupabaseClient {
  anon ??= createClient(SUPABASE_URL, SUPABASE_KEY, { auth: { persistSession: false, autoRefreshToken: false } });
  return anon;
}

/** Everything a page needs for one publishing day, as an anonymous visitor sees it. */
export async function loadDay(date?: string, now = new Date()): Promise<Day> {
  const today = publishingWindow(now).date;
  const w = windowFor(date ?? today);
  const db = publicClient();
  const [fixtures, tips, predictions] = await Promise.all([coveredFixtures(db, w), visibleTips(db, w), visiblePredictions(db, w)]);
  const fixtureMap = new Map(fixtures.map((f) => [f.id, f]));
  const slips = await daySlips(db, w, new Map(tips.map((t) => [t.id, t])), fixtureMap);
  return {
    window: w, today, fixtures, tips: groupTips(tips),
    predictions: new Map(predictions.map((p) => [p.fixtureId, p])), slips, preview: false, member: false,
  };
}

