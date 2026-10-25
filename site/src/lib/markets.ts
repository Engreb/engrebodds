// Plain-English labels for selections. Published tips arrive as legs (market_code, selection, line);
// predictions are keyed "MARKET|selection|line"; previews as "OU O 1.5 & BTTS Y".

export interface Leg {
  market: string;
  selection: string;
  line?: number | null;
}

const fmtLine = (l: number | null | undefined) => (l == null ? "" : Number(l).toFixed(1));

export function legLabel({ market, selection, line }: Leg): string {
  const l = fmtLine(line);
  switch (market) {
    case "1X2":
      return { "1": "Home win", X: "Draw", "2": "Away win" }[selection] ?? selection;
    case "DC":
      return { "1X": "Home or draw", X2: "Away or draw", "12": "Either team wins" }[selection] ?? selection;
    case "OU":
      return `${selection === "O" ? "Over" : "Under"} ${l} goals`;
    case "BTTS":
      return selection === "Y" ? "Both teams score (GG)" : "Not both score (NG)";
    case "CS":
      return `Correct score ${selection}`;
    case "HT_1X2":
      return `Half time: ${legLabel({ market: "1X2", selection }).toLowerCase()}`;
    case "HT_OU":
      return `Half time: ${selection === "O" ? "over" : "under"} ${l} goals`;
    case "HT_CS":
      return `Half-time score ${selection}`;
    case "CLEAN_SHEET":
      return selection === "H" ? "Home clean sheet" : "Away clean sheet";
    case "TEAM_OU": {
      const side = selection[0] === "H" ? "Home" : "Away";
      return `${side} ${selection[1] === "O" ? "over" : "under"} ${l} goals`;
    }
    default:
      return [market, selection, l].filter(Boolean).join(" ");
  }
}

export function comboLabel(legs: Leg[]): string {
  return legs.map(legLabel).join(" & ");
}

/** "OU|O|2.5" -> leg */
export function parseKey(key: string): Leg {
  const [market, selection, line] = key.split("|");
  return { market, selection, line: line === undefined ? null : Number(line) };
}

/** "DC 12 & OU U 4.5" (preview payloads) -> legs */
export function parseText(text: string): Leg[] {
  return text.split(" & ").map((part) => {
    const [market, selection, line] = part.trim().split(" ");
    return { market, selection, line: line === undefined ? null : Number(line) };
  });
}

export const SLOT_LABEL: Record<string, string> = {
  result: "Result",
  goals: "Goals",
  btts: "GG/NG",
  builder: "Bet builder",
};

/** Market pages: which prediction keys each page chooses between. */
export const MARKET_PAGES = {
  "gg-ng": { title: "GG/NG predictions today", h1: "GG/NG tips", keys: ["BTTS|Y", "BTTS|N"],
    lede: "Both-teams-to-score picks for every covered fixture, from each side's expected goals." },
  "over-1-5": { title: "Over 1.5 goals predictions today", h1: "Over 1.5 goals tips", keys: ["OU|O|1.5"],
    lede: "How likely each match is to see at least two goals." },
  "over-under-2-5": { title: "Over/under 2.5 predictions today", h1: "Over / under 2.5 tips", keys: ["OU|O|2.5", "OU|U|2.5"],
    lede: "The stronger of over or under 2.5 goals for each fixture." },
  "double-chance": { title: "Double chance predictions today", h1: "Double chance tips", keys: ["DC|1X", "DC|X2", "DC|12"],
    lede: "1X, X2 or 12, whichever the model rates highest." },
} as const;
export type MarketPage = keyof typeof MARKET_PAGES;

export const pct = (p: number, digits = 0) => `${(p * 100).toFixed(digits)}%`;
export const odds = (o: number | null | undefined) => (o == null ? "–" : Number(o).toFixed(2));
export const fairOdds = (p: number) => Math.max(1.01, Math.round((0.94 / p) * 100) / 100);

/** Whether a full-time selection won, from the 90-minute score (half-time markets aren't settled here). */
export function settles({ market, selection, line }: Leg, [h, a]: [number, number]): boolean | null {
  const goals = h + a;
  switch (market) {
    case "1X2": return selection === "1" ? h > a : selection === "2" ? a > h : h === a;
    case "DC": return selection === "1X" ? h >= a : selection === "X2" ? a >= h : h !== a;
    case "OU": return line == null ? null : selection === "O" ? goals > line : goals < line;
    case "BTTS": return (h > 0 && a > 0) === (selection === "Y");
    case "CS": return selection === `${h}-${a}`;
    default: return null;
  }
}

/** The strongest of a page's selections for one match, from its visible probabilities. */
export function bestOf(probs: Record<string, number>, keys: readonly string[]): { key: string; p: number } | null {
  let best: { key: string; p: number } | null = null;
  for (const k of keys) if (probs[k] != null && (!best || probs[k] > best.p)) best = { key: k, p: probs[k] };
  return best;
}
