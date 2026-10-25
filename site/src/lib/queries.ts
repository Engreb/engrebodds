// Queries and row mappers, given a client. No environment access here, so tests can import it.

import type { SupabaseClient } from "@supabase/supabase-js";
import type { Window } from "./day";
import { assignSlugs, slugify } from "./slug";
import { FINISHED, type Fixture, type League, type Prediction, type Result, type Slip, type Tip } from "./types";

function must<T>(res: { data: T | null; error: { message: string } | null }, what: string): T {
  if (res.error) throw new Error(`${what}: ${res.error.message}`);
  return res.data as T;
}

const FIXTURE_COLS =
  "id, kickoff_at, status, season, home_90, away_90, " +
  "league:leagues!inner(id, name, country, slug, display_order), " +
  "home:teams!fixtures_home_team_id_fkey(id, name), away:teams!fixtures_away_team_id_fkey(id, name)";

const watDate = (iso: string) => new Date(new Date(iso).getTime() + 3_600_000).toISOString().slice(0, 10);

export function leagueOf(l: { id: number; name: string; country: string; slug: string | null; display_order: number | null }): League {
  return { id: l.id, name: l.name, country: l.country, slug: l.slug ?? slugify(`${l.country}-${l.name}`), order: l.display_order };
}

export function fixtureOf(r: any): Omit<Fixture, "slug" | "tipCount" | "hasFree" | "hasBanker"> {
  const finished = FINISHED.has(r.status);
  return {
    id: r.id,
    kickoff: r.kickoff_at,
    date: watDate(r.kickoff_at),
    status: r.status,
    finished,
    season: r.season,
    home: r.home,
    away: r.away,
    league: leagueOf(r.league),
    score: finished && r.home_90 != null ? [r.home_90, r.away_90] : null,
  };
}

export function tipOf(r: any): Tip {
  return {
    id: String(r.id),
    fixtureId: r.fixture_id,
    slot: r.slot,
    legs: [...(r.tip_legs ?? [])]
      .sort((a, b) => a.leg_no - b.leg_no)
      .map((l) => ({ market: l.market_code, selection: l.selection, line: l.line == null ? null : Number(l.line) })),
    p: Number(r.model_prob),
    fair: Number(r.fair_odds),
    price: r.ref_odds == null ? null : Number(r.ref_odds),
    capturedAt: r.ref_captured_at,
    isFree: r.is_free,
    isBanker: r.is_banker,
    result: r.result as Result,
    pSource: r.p_source,
  };
}

/** Fixtures we cover in a window (they have a prediction or tips), with what anyone may know about their tips. */
export async function coveredFixtures(db: SupabaseClient, w: Window): Promise<Fixture[]> {
  const status = must(
    await db.rpc("fixture_tip_status", { p_from: w.start.toISOString(), p_to: w.end.toISOString() }),
    "fixture_tip_status",
  ) as { fixture_id: number; predicted: boolean; tips: number; has_free: boolean; has_banker: boolean }[];
  if (!status.length) return [];
  const byId = new Map(status.map((s) => [s.fixture_id, s]));
  const rows = must(
    await db.from("fixtures").select(FIXTURE_COLS).gte("kickoff_at", w.start.toISOString()).lt("kickoff_at", w.end.toISOString())
      .order("kickoff_at"),
    "fixtures",
  ) as unknown[];
  const base = rows.map(fixtureOf).filter((f) => byId.has(f.id));
  const slugs = assignSlugs(base.map((f) => ({ id: f.id, home: f.home.name, away: f.away.name, date: f.date })));
  return base.map((f) => {
    const s = byId.get(f.id)!;
    return { ...f, slug: slugs.get(f.id)!, tipCount: s.tips, hasFree: s.has_free, hasBanker: s.has_banker };
  });
}

const TIP_COLS =
  "id, fixture_id, slot, is_banker, is_free, model_prob, fair_odds, ref_odds, ref_captured_at, p_source, result, " +
  "tip_legs(leg_no, market_code, selection, line)";

export async function visibleTips(db: SupabaseClient, w: Window): Promise<Tip[]> {
  const rows = must(
    await db.from("tips").select(TIP_COLS).eq("product", "likely")
      .gte("kickoff_at", w.start.toISOString()).lt("kickoff_at", w.end.toISOString()),
    "tips",
  ) as unknown[];
  return rows.map(tipOf);
}

export async function visiblePredictions(db: SupabaseClient, w: Window): Promise<Prediction[]> {
  const rows = must(
    await db.from("predictions")
      .select("fixture_id, lambda_home, lambda_away, probs, final, cs_top, computed_at, fixtures!inner(kickoff_at)")
      .gte("fixtures.kickoff_at", w.start.toISOString()).lt("fixtures.kickoff_at", w.end.toISOString())
      .order("computed_at", { ascending: false }),
    "predictions",
  ) as { fixture_id: number; lambda_home: number; lambda_away: number; probs: Record<string, number>;
         final: Record<string, { p: number }> | null; cs_top: { score: string; p: number }[] | null }[];
  const seen = new Set<number>();
  const out: Prediction[] = [];
  for (const r of rows) {
    if (seen.has(r.fixture_id)) continue;
    seen.add(r.fixture_id);
    out.push(predictionOf(r));
  }
  return out;
}

/** p_final (market) where it exists, otherwise the model: the same probabilities the tips used. */
export function predictionOf(r: { fixture_id: number; lambda_home: number; lambda_away: number; probs: Record<string, number>;
                                  final?: Record<string, { p: number }> | null; cs_top?: { score: string; p: number }[] | null }): Prediction {
  const probs = { ...r.probs };
  for (const [k, v] of Object.entries(r.final ?? {})) probs[k] = v.p;
  return { fixtureId: r.fixture_id, lambdaHome: Number(r.lambda_home), lambdaAway: Number(r.lambda_away), probs, csTop: r.cs_top ?? [] };
}

export async function daySlips(db: SupabaseClient, w: Window, tips: Map<string, Tip>, fixtures: Map<number, Fixture>): Promise<Slip[]> {
  const status = must(await db.rpc("slip_status", { p_date: w.date }), "slip_status") as {
    kind: string; legs: number; combined_odds: number; ref_odds: number | null; ref_captured_at: string | null;
    combined_prob: number; is_free: boolean; locks_at: string; result: Result }[];
  const visible = must(
    await db.from("slips").select("kind, slip_legs(tip_id, odds)").eq("slip_date", w.date),
    "slips",
  ) as { kind: string; slip_legs: { tip_id: number; odds: number }[] }[];
  const legsByKind = new Map(visible.map((s) => [s.kind, s.slip_legs]));
  return status.map((s) => {
    const legs = legsByKind.get(s.kind);
    return {
      kind: s.kind, legs: s.legs, combinedOdds: Number(s.combined_odds), price: s.ref_odds == null ? null : Number(s.ref_odds),
      capturedAt: s.ref_captured_at, chance: Number(s.combined_prob), isFree: s.is_free, locksAt: s.locks_at, result: s.result,
      selections: legs
        ? legs.flatMap((l) => {
            const tip = tips.get(String(l.tip_id));
            return tip ? [{ tip, fixture: fixtures.get(tip.fixtureId), price: Number(l.odds) }] : [];
          })
        : null,
    };
  });
}

export function groupTips(tips: Tip[]): Map<number, Tip[]> {
  const SLOT_ORDER = ["result", "goals", "btts", "builder"];
  const by = new Map<number, Tip[]>();
  for (const t of tips) by.set(t.fixtureId, [...(by.get(t.fixtureId) ?? []), t]);
  for (const list of by.values()) list.sort((a, b) => SLOT_ORDER.indexOf(a.slot) - SLOT_ORDER.indexOf(b.slot));
  return by;
}
