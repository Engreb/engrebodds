import { describe, expect, it } from "vitest";
import { decide, hasSession } from "../src/lib/cache";
import { publishingWindow, shiftDate, watTime } from "../src/lib/day";
import { previewText, summarise, type Played } from "../src/lib/facts";
import { comboLabel, fairOdds, legLabel, parseKey, parseText } from "../src/lib/markets";
import { assignSlugs, matchSlug, parseMatchSlug, slugify } from "../src/lib/slug";

describe("publishing window (same cases as model/engrebodds_model/live/tips.py)", () => {
  it("runs 06:45 to 06:45 UTC", () => {
    const w = publishingWindow(new Date("2026-10-25T07:30:00Z"));
    expect(w.date).toBe("2026-10-25");
    expect(w.start.toISOString()).toBe("2026-10-25T06:45:00.000Z");
    expect(w.end.toISOString()).toBe("2026-10-26T06:45:00.000Z");
  });
  it("belongs to the previous day before 06:45", () => {
    expect(publishingWindow(new Date("2026-10-25T06:44:59Z")).date).toBe("2026-10-24");
    expect(publishingWindow(new Date("2026-10-25T06:45:00Z")).date).toBe("2026-10-25");
  });
  it("crosses month ends", () => {
    expect(publishingWindow(new Date("2026-11-01T03:00:00Z")).date).toBe("2026-10-31");
    expect(shiftDate("2026-12-31", 1)).toBe("2027-01-01");
  });
  it("shows WAT", () => expect(watTime("2026-10-25T10:00:00Z")).toBe("11:00"));
});

describe("market labels", () => {
  it("labels published legs, prediction keys and preview text the same way", () => {
    expect(legLabel({ market: "OU", selection: "O", line: 2.5 })).toBe("Over 2.5 goals");
    expect(legLabel(parseKey("DC|X2"))).toBe("Away or draw");
    expect(comboLabel(parseText("DC 12 & OU U 4.5"))).toBe("Either team wins & Under 4.5 goals");
    expect(comboLabel(parseText("BTTS N"))).toBe("Not both score (NG)");
    expect(legLabel(parseKey("TEAM_OU|AO|1.5"))).toBe("Away over 1.5 goals");
  });
  it("fair odds use the 6% margin", () => {
    expect(fairOdds(0.5)).toBe(1.88);
    expect(fairOdds(0.99)).toBe(1.01);
  });
});

describe("match slugs", () => {
  it("are readable and reversible", () => {
    expect(slugify("Atlético Madrid")).toBe("atletico-madrid");
    expect(slugify("Brighton & Hove Albion")).toBe("brighton-and-hove-albion");
    const s = matchSlug("Rivers United", "Enyimba", "2026-10-25");
    expect(s).toBe("rivers-united-vs-enyimba-2026-10-25");
    expect(parseMatchSlug(s)).toEqual({ base: s, date: "2026-10-25", id: null });
    expect(parseMatchSlug(`${s}-77`)?.id).toBe(77);
    expect(parseMatchSlug("2026-10-25")).toBeNull();
  });
  it("disambiguate two fixtures with the same names on one day", () => {
    const slugs = assignSlugs([
      { id: 1, home: "Arsenal", away: "Dinamo", date: "2026-10-25" },
      { id: 2, home: "Arsenal", away: "Dinamo", date: "2026-10-25" },
      { id: 3, home: "Arsenal", away: "Dinamo", date: "2026-10-26" },
    ]);
    expect([slugs.get(1), slugs.get(2), slugs.get(3)]).toEqual([
      "arsenal-vs-dinamo-2026-10-25-1", "arsenal-vs-dinamo-2026-10-25-2", "arsenal-vs-dinamo-2026-10-26"]);
  });
});

describe("cache policy", () => {
  const req = (url: string, init: RequestInit = {}) => new Request(`https://engrebodds.com${url}`, init);
  it("caches anonymous pages for 60s with stale-while-revalidate", () => {
    expect(decide(req("/predictions"))).toEqual({ kind: "cache", maxAge: 60, swr: 600 });
    expect(decide(req("/predictions?league=england-premier-league")).kind).toBe("cache");
  });
  it("never caches members, authorised requests, posts or previews", () => {
    expect(decide(req("/", { headers: { cookie: "a=1; eo_session=xyz" } })).kind).toBe("private");
    expect(decide(req("/", { headers: { authorization: "Bearer t" } })).kind).toBe("private");
    expect(decide(req("/", { method: "POST" })).kind).toBe("private");
    expect(decide(req("/"), { preview: true }).kind).toBe("private");
    expect(hasSession("eo_sessionx=1")).toBe(false);
  });
  it("drops query parameters outside the allowlist, so they can't split the cache", () => {
    expect(decide(req("/predictions?utm_source=wa&league=x"))).toEqual({ kind: "redirect", location: "/predictions?league=x" });
    expect(decide(req("/?fbclid=1"))).toEqual({ kind: "redirect", location: "/" });
  });
});

describe("match preview text", () => {
  const p = (outcome: "W" | "D" | "L", gf: number, ga: number): Played =>
    ({ kickoff: "2024-08-11T15:00:00Z", opponent: "X", home: true, gf, ga, outcome });
  const teams = { home: { id: 1, name: "Rivers United" }, away: { id: 2, name: "Enyimba" },
                  league: { id: 1, name: "NPFL", country: "Nigeria", slug: "npfl", order: 1 } };
  it("uses facts only, never model numbers or tip words", () => {
    const text = previewText(teams, {
      home: summarise([p("W", 2, 0), p("D", 1, 1), p("L", 0, 1)]),
      away: summarise([]),
      h2h: [p("W", 1, 0)],
      table: null,
    });
    expect(text).toContain("Rivers United have 1 win, 1 draw and 1 defeat");
    expect(text).toContain("no recent results on record for Enyimba");
    expect(text).toMatch(/last 1 meeting on record, Rivers United won 1/);
    expect(text).not.toMatch(/%|xG|probab|tip|banker|odds|likely|predict/i);
  });
});

describe("settling market-page picks", async () => {
  const { settles, bestOf } = await import("../src/lib/markets");
  it("settles the full-time markets from the 90-minute score", () => {
    expect(settles(parseKey("OU|O|2.5"), [2, 1])).toBe(true);
    expect(settles(parseKey("OU|U|2.5"), [2, 1])).toBe(false);
    expect(settles(parseKey("BTTS|N"), [1, 0])).toBe(true);
    expect(settles(parseKey("DC|X2"), [1, 1])).toBe(true);
    expect(settles(parseKey("CS|2-1"), [2, 1])).toBe(true);
    expect(settles(parseKey("HT_1X2|1"), [2, 1])).toBeNull();
  });
  it("picks the most likely selection of a page", () => {
    expect(bestOf({ "BTTS|Y": 0.41, "BTTS|N": 0.59 }, ["BTTS|Y", "BTTS|N"])).toEqual({ key: "BTTS|N", p: 0.59 });
    expect(bestOf({}, ["BTTS|Y"])).toBeNull();
  });
});

describe("bet builder", async () => {
  const { buildSlip } = await import("../src/lib/builder");
  const m = (id: number, p: Record<string, number>) => ({ id, match: `M${id}`, meta: "", p });
  const matches = [m(1, { "1X2|1": 0.8, "DC|1X": 0.9 }), m(2, { "OU|O|1.5": 0.75 }), m(3, { "BTTS|Y": 0.6 }), m(4, { "1X2|1": 0.95 })];
  it("takes the most likely first, one per match, legs at 1.12+, until the target", () => {
    const s = buildSlip(matches, { target: 2, minP: 0.65, keys: ["1X2|1", "DC|1X", "OU|O|1.5", "BTTS|Y"] });
    // 0.95 -> fair 0.99 (below 1.12, dropped); M1 DC 0.9 (1.04, dropped); M1 1X2 0.8 (1.18); M2 0.75 (1.25); ...
    expect(s.legs.map((l) => [l.id, l.key])).toEqual([[1, "1X2|1"], [2, "OU|O|1.5"]]);
    expect(s.reached).toBe(false);
    expect(s.chance).toBeCloseTo(0.6);
  });
  it("respects the minimum probability and chosen markets", () => {
    expect(buildSlip(matches, { target: 5, minP: 0.65, keys: ["BTTS|Y"] }).legs).toEqual([]);
  });
});

describe("CSV export", async () => {
  const { toCsv } = await import("../src/lib/csv");
  it("quotes and defuses spreadsheet formulas", () => {
    expect(toCsv(["a", "b"], [["x,y", '=HYPERLINK("e")'], [-1.5, null]])).toBe('a,b\r\n"x,y","\'=HYPERLINK(""e"")"\r\n-1.5,\r\n');
  });
});

describe("OG cards never draw a locked pick", async () => {
  const { bankerCard, slipCard } = await import("../src/lib/og-cards");
  const fixture = { id: 1, kickoff: "2026-10-25T13:00:00Z", date: "2026-10-25", status: "NS", finished: false, season: 2026,
    home: { id: 1, name: "Kano Kings" }, away: { id: 2, name: "Enugu Eagles" },
    league: { id: 1, name: "NPFL", country: "Nigeria", slug: "npfl", order: 1 }, score: null, slug: "x", tipCount: 4, hasFree: false, hasBanker: true };
  const tip = { id: "1", fixtureId: 1, slot: "goals", legs: [{ market: "OU", selection: "U", line: 3.5 }], p: 0.61, fair: 1.54,
    price: 3.17, capturedAt: null, isFree: false, isBanker: true, result: "pending" as const, pSource: "market" };
  it("banker: match only when locked, pick and price when visible", () => {
    const locked = JSON.stringify(bankerCard(fixture, undefined));
    expect(locked).toContain("Kano Kings v Enugu Eagles");
    expect(locked).not.toMatch(/Under 3\.5|3\.17|1\.54/);
    const open = bankerCard(fixture, tip);
    expect(open.lines[1]).toBe("Under 3.5 goals");
    expect(open.big).toBe("3.17");
  });
  it("slips: count, price and chance only", () => {
    const slip = { kind: "odds2", legs: 2, combinedOdds: 4.6, price: 4.6, capturedAt: null, chance: 0.3368, isFree: false,
      locksAt: "", result: "pending" as const, selections: [{ tip, fixture, price: 3.17 }] };
    const card = JSON.stringify(slipCard("odds2", slip));
    expect(card).toContain("33.7%");
    expect(card).not.toMatch(/Kano|Under 3\.5|3\.17/);
  });
});
