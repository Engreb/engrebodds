// The bet builder's selection rule (pure, shared by the page script and the tests). Same shape as the
// daily slips: most likely first, one selection per match, legs priced at 1.12 or more, stop at the target.
import { fairOdds } from "./markets";

export interface BuilderMatch { id: number; match: string; meta: string; p: Record<string, number> }
export interface BuilderLeg { id: number; match: string; meta: string; key: string; p: number; fair: number }
export interface BuilderSlip { legs: BuilderLeg[]; odds: number; chance: number; reached: boolean }

export const MIN_LEG_PRICE = 1.12;

export function buildSlip(matches: BuilderMatch[], opts: { target: number; minP: number; keys: string[] }): BuilderSlip {
  const cands: BuilderLeg[] = [];
  for (const m of matches) {
    for (const key of opts.keys) {
      const p = m.p[key];
      if (p == null || p < opts.minP) continue;
      const fair = fairOdds(p);
      if (fair >= MIN_LEG_PRICE) cands.push({ id: m.id, match: m.match, meta: m.meta, key, p, fair });
    }
  }
  cands.sort((a, b) => b.p - a.p || a.id - b.id);
  const used = new Set<number>();
  const legs: BuilderLeg[] = [];
  let price = 1, chance = 1;
  for (const c of cands) {
    if (price >= opts.target) break;
    if (used.has(c.id)) continue;
    used.add(c.id);
    legs.push(c);
    price *= c.fair;
    chance *= c.p;
  }
  return { legs, odds: Math.round(price * 100) / 100, chance, reached: price >= opts.target };
}
