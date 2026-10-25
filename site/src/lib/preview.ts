// Preview mode (local dev only, SITE_PREVIEW=true): today's unpublished tip preview, read with the
// service key, then filtered in code by the same rules RLS applies to the real tables. Never imported
// in production builds: callers reach it through `import.meta.env.DEV`.

import { createClient } from "@supabase/supabase-js";
import { SUPABASE_SERVICE_KEY, SUPABASE_URL } from "astro:env/server";
import { coveredFixtures, groupTips, predictionOf } from "./queries";
import { publishingWindow, windowFor } from "./day";
import { parseText } from "./markets";
import type { Day, Prediction, Slip, Tip } from "./types";

interface TipPayload { slot: string; tip: string; p: number; fair: number; price_1xbet: number | null;
                       captured_at: string | null; banker: boolean; p_source: string }
interface SlipPayload { kind: string; price_1xbet: number; chance_all_land: number; captured_at: string; locks_at: string;
                        legs: { fixture_id: number; slot: string; price_1xbet: number }[] }

export async function loadPreviewDay(date: string | undefined, member: boolean, now = new Date()): Promise<Day> {
  if (!SUPABASE_SERVICE_KEY) throw new Error("Preview mode needs SUPABASE_SERVICE_KEY in site/.env.local");
  const db = createClient(SUPABASE_URL, SUPABASE_SERVICE_KEY, { auth: { persistSession: false, autoRefreshToken: false } });
  const today = publishingWindow(now).date;
  const w = windowFor(date ?? today);

  const { data: rows, error } = await db.from("tip_previews").select("kind, fixture_id, payload")
    .eq("window_start", w.start.toISOString());
  if (error) throw new Error(`tip_previews: ${error.message}`);

  const tips: Tip[] = [];
  for (const r of rows.filter((r) => r.kind === "tip")) {
    for (const t of (r.payload.tips as TipPayload[])) {
      tips.push({
        id: `preview-${r.fixture_id}-${t.slot}`, fixtureId: r.fixture_id, slot: t.slot, legs: parseText(t.tip), p: t.p,
        fair: t.fair, price: t.price_1xbet, capturedAt: t.captured_at, isFree: Boolean(r.payload.free), isBanker: t.banker,
        result: "pending", pSource: t.p_source,
      });
    }
  }

  const { data: preds, error: perr } = await db.from("predictions")
    .select("fixture_id, lambda_home, lambda_away, probs, final, cs_top, computed_at, fixtures!inner(kickoff_at)")
    .gte("fixtures.kickoff_at", w.start.toISOString()).lt("fixtures.kickoff_at", w.end.toISOString())
    .order("computed_at", { ascending: false });
  if (perr) throw new Error(`predictions: ${perr.message}`);
  const predictions = new Map<number, Prediction>();
  for (const p of preds) if (!predictions.has(p.fixture_id)) predictions.set(p.fixture_id, predictionOf(p));

  // Fixture list and facts: the public view, with tip flags taken from the preview instead.
  const fixtures = (await coveredFixtures(createClient(SUPABASE_URL, SUPABASE_SERVICE_KEY), w)).map((f) => {
    const mine = tips.filter((t) => t.fixtureId === f.id);
    return { ...f, tipCount: mine.length, hasFree: mine.some((t) => t.isFree), hasBanker: mine.some((t) => t.isBanker) };
  });
  const byFixture = new Map(fixtures.map((f) => [f.id, f]));

  const slips: Slip[] = rows.filter((r) => r.kind === "slip").map((r) => {
    const s = r.payload as SlipPayload;
    return {
      kind: s.kind, legs: s.legs.length, combinedOdds: s.price_1xbet, price: s.price_1xbet, capturedAt: s.captured_at,
      chance: s.chance_all_land, isFree: false, locksAt: s.locks_at, result: "pending",
      selections: s.legs.flatMap((l) => {
        const tip = tips.find((t) => t.fixtureId === l.fixture_id && t.slot === l.slot);
        return tip ? [{ tip, fixture: byFixture.get(l.fixture_id), price: l.price_1xbet }] : [];
      }),
    };
  });

  // The visitor's view: the same rules as RLS (nothing in a preview is graded yet).
  const freeFixtures = new Set(tips.filter((t) => t.isFree).map((t) => t.fixtureId));
  const seeTip = (t: Tip) => member || t.isFree;
  const seePrediction = (id: number) => member || freeFixtures.has(id) || Boolean(byFixture.get(id)?.finished);
  return {
    window: w, today, fixtures,
    tips: groupTips(tips.filter(seeTip)),
    predictions: new Map([...predictions].filter(([id]) => seePrediction(id))),
    slips: slips.map((s) => (member || s.isFree ? s : { ...s, selections: null })),
    preview: true, member,
  };
}
