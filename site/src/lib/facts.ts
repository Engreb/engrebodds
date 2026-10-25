// Match-page facts that are public on any football site: recent form, head-to-head, goals, table.
// Built from finished results only, never from the model, so they can be shown (and indexed) for
// locked matches without hinting at a tip.

import type { SupabaseClient } from "@supabase/supabase-js";
import { fixtureOf } from "./queries";
import type { Fixture } from "./types";

export interface Played { kickoff: string; opponent: string; home: boolean; gf: number; ga: number; outcome: "W" | "D" | "L" }
export interface TeamFacts { played: Played[]; form: Played[]; gfAvg: number | null; gaAvg: number | null }
export interface Standing { rank: number; played: number; points: number; gf: number; ga: number; updatedAt: string }
export interface Facts { home: TeamFacts; away: TeamFacts; h2h: Played[]; table: { home?: Standing; away?: Standing } | null; preview: string }

const COLS = "id, kickoff_at, status, season, home_90, away_90, league:leagues!inner(id, name, country, slug, display_order), " +
  "home:teams!fixtures_home_team_id_fkey(id, name), away:teams!fixtures_away_team_id_fkey(id, name)";
const FINISHED = "(FT,AET,PEN)";
const TABLE_MAX_AGE_DAYS = 10;

function asPlayed(f: ReturnType<typeof fixtureOf>, teamId: number): Played | null {
  if (!f.score) return null;
  const home = f.home.id === teamId;
  const [gf, ga] = home ? f.score : [f.score[1], f.score[0]];
  return { kickoff: f.kickoff, opponent: home ? f.away.name : f.home.name, home, gf, ga, outcome: gf > ga ? "W" : gf < ga ? "L" : "D" };
}

async function recent(db: SupabaseClient, teamId: number, before: string, n: number): Promise<Played[]> {
  const { data, error } = await db.from("fixtures").select(COLS)
    .or(`home_team_id.eq.${teamId},away_team_id.eq.${teamId}`).filter("status", "in", FINISHED)
    .lt("kickoff_at", before).order("kickoff_at", { ascending: false }).limit(n);
  if (error) throw new Error(`form: ${error.message}`);
  return (data as unknown[]).map(fixtureOf).flatMap((f) => asPlayed(f, teamId) ?? []);
}

export function summarise(played: Played[]): TeamFacts {
  const avg = (xs: number[]) => (xs.length ? Math.round((xs.reduce((a, b) => a + b, 0) / xs.length) * 10) / 10 : null);
  return { played, form: played.slice(0, 5), gfAvg: avg(played.map((p) => p.gf)), gaAvg: avg(played.map((p) => p.ga)) };
}

const wdl = (ps: Played[]) => {
  const c = { W: 0, D: 0, L: 0 };
  for (const p of ps) c[p.outcome]++;
  return `${c.W} win${c.W === 1 ? "" : "s"}, ${c.D} draw${c.D === 1 ? "" : "s"} and ${c.L} defeat${c.L === 1 ? "" : "s"}`;
};

/** A short template preview from facts only: no probabilities, xG or tip wording. */
export function previewText(f: Pick<Fixture, "home" | "away" | "league">, facts: Omit<Facts, "preview">): string {
  const parts: string[] = [];
  const side = (name: string, t: TeamFacts) =>
    t.form.length
      ? `${name} have ${wdl(t.form)} in their last ${t.form.length} recorded matches, scoring ${t.gfAvg} and conceding ${t.gaAvg} a game on average over their last ${t.played.length}.`
      : `We have no recent results on record for ${name}.`;
  parts.push(side(f.home.name, facts.home), side(f.away.name, facts.away));
  if (facts.h2h.length) {
    const homeWins = facts.h2h.filter((p) => p.outcome === "W").length;
    const awayWins = facts.h2h.filter((p) => p.outcome === "L").length;
    parts.push(`In their last ${facts.h2h.length} meeting${facts.h2h.length === 1 ? "" : "s"} on record, ${f.home.name} won ${homeWins}, ${f.away.name} won ${awayWins} and ${facts.h2h.length - homeWins - awayWins} ended level.`);
  } else {
    parts.push(`We have no previous meetings between these sides on record.`);
  }
  if (facts.table?.home && facts.table?.away) {
    parts.push(`In the ${f.league.name} table, ${f.home.name} are ${ordinal(facts.table.home.rank)} on ${facts.table.home.points} points and ${f.away.name} ${ordinal(facts.table.away.rank)} on ${facts.table.away.points}.`);
  }
  return parts.join(" ");
}

export function ordinal(n: number): string {
  const s = ["th", "st", "nd", "rd"], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

export async function matchFacts(db: SupabaseClient, f: Fixture, now = new Date()): Promise<Facts> {
  const before = f.kickoff;
  const [homeP, awayP, h2hRes, tableRes] = await Promise.all([
    recent(db, f.home.id, before, 10),
    recent(db, f.away.id, before, 10),
    db.from("fixtures").select(COLS)
      .or(`and(home_team_id.eq.${f.home.id},away_team_id.eq.${f.away.id}),and(home_team_id.eq.${f.away.id},away_team_id.eq.${f.home.id})`)
      .filter("status", "in", FINISHED).lt("kickoff_at", before).order("kickoff_at", { ascending: false }).limit(5),
    db.from("standings").select("team_id, rank, played, points, goals_for, goals_against, updated_at")
      .eq("league_id", f.league.id).eq("season", f.season).in("team_id", [f.home.id, f.away.id]),
  ]);
  if (h2hRes.error) throw new Error(`h2h: ${h2hRes.error.message}`);
  if (tableRes.error) throw new Error(`standings: ${tableRes.error.message}`);
  const h2h = (h2hRes.data as unknown[]).map(fixtureOf).flatMap((x) => asPlayed(x, f.home.id) ?? []);
  // Only a current table: on the free API plan standings are old, and a stale table would mislead.
  const fresh = (tableRes.data ?? []).filter((r) => now.getTime() - Date.parse(r.updated_at) < TABLE_MAX_AGE_DAYS * 86_400_000);
  const st = (id: number) => {
    const r = fresh.find((x) => x.team_id === id);
    return r ? { rank: r.rank, played: r.played, points: r.points, gf: r.goals_for, ga: r.goals_against, updatedAt: r.updated_at } : undefined;
  };
  const table = fresh.length ? { home: st(f.home.id), away: st(f.away.id) } : null;
  const base = { home: summarise(homeP), away: summarise(awayP), h2h, table };
  return { ...base, preview: previewText(f, base) };
}
