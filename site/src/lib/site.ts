// Fixed site content shared by several pages.
import type { Day, Fixture, Tip } from "./types";

export const PLANS = [
  { id: "weekly", name: "Weekly", ngn: 2500, days: "7 days", per: "₦357 a day" },
  { id: "monthly", name: "Monthly", ngn: 7500, days: "30 days", per: "₦250 a day", best: true },
  { id: "quarter", name: "3 months", ngn: 18000, days: "90 days", per: "₦200 a day" },
];
export const naira = (n: number) => `₦${n.toLocaleString("en-NG")}`;

export const BOOKMAKERS = [
  { slug: "sportybet", name: "SportyBet" },
  { slug: "bet9ja", name: "Bet9ja" },
  { slug: "1xbet", name: "1xBet" },
  { slug: "betking", name: "BetKing" },
  { slug: "betway", name: "Betway" },
  { slug: "msport", name: "MSport" },
  { slug: "nairabet", name: "NairaBet" },
];

export const SLIP_PAGES = {
  "2-odds-today": { kind: "odds2", target: 2 },
  "5-odds-today": { kind: "odds5", target: 5 },
  "10-odds-today": { kind: "odds10", target: 10 },
} as const;

/** The day's banker: the fixture holding it, and the tip itself when the visitor may see it. */
export function banker(day: Day): { fixture?: Fixture; tip?: Tip } {
  for (const tips of day.tips.values()) {
    const tip = tips.find((t) => t.isBanker);
    if (tip) return { fixture: day.fixtures.find((f) => f.id === tip.fixtureId), tip };
  }
  return { fixture: day.fixtures.find((f) => f.hasBanker) };
}

export const slipOf = (day: Day, kind: string) => day.slips.find((s) => s.kind === kind);

/** Public contact address for the About page. Unset until Emmanuel confirms one (the page says so). */
export const CONTACT_EMAIL: string | null = null;

/** Public proof of the record (model/engrebodds_model/live/proof.py). Fill in once the channel and repo exist. */
export const PROOF: { telegram: string | null; repo: string | null } = { telegram: null, repo: null };
