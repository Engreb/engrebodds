// What each OG card says (pure, unit-tested): only what the page shows a visitor. A locked pick is never drawn.
import { watTime } from "./day";
import { comboLabel, odds, pct } from "./markets";
import type { Card } from "./og";
import type { Fixture, Slip, Tip } from "./types";

export function bankerCard(fixture: Fixture | undefined, tip: Tip | undefined): Card {
  if (!fixture) return { eyebrow: "Banker of the day", title: "Published at 07:45 WAT", lines: ["Our most confident pick, graded after full time"] };
  return {
    eyebrow: "Banker of the day",
    title: `${fixture.home.name} v ${fixture.away.name}`,
    lines: [`${fixture.league.name} · ${watTime(fixture.kickoff)} WAT`, tip ? comboLabel(tip.legs) : "Pick for members · on the record after full time"],
    big: tip ? odds(tip.price ?? tip.fair) : undefined,
  };
}

export const SLIP_TITLES: Record<string, string> = { odds2: "2 odds today", odds5: "5 odds today", odds10: "10 odds today", low_risk: "Lowest-risk slip" };

/** Slips: leg count, combined price and chance all legs land. Selections are never drawn, open or locked. */
export function slipCard(kind: string, slip: Slip | undefined): Card {
  return {
    eyebrow: "Today's slip",
    title: SLIP_TITLES[kind] ?? kind,
    lines: slip ? [`${slip.legs} selections · chance every leg lands ${pct(slip.chance, 1)}`, "Graded on the public track record"]
                : ["Built at 07:45 WAT from the day's priced tips"],
    big: slip ? odds(slip.price ?? slip.combinedOdds) : undefined,
  };
}
