import type { Leg } from "./markets";
import type { Window } from "./day";

export type Result = "pending" | "won" | "lost" | "void";

export interface Team { id: number; name: string }
export interface League { id: number; name: string; country: string; slug: string; order: number | null }

export interface Fixture {
  id: number;
  kickoff: string; // ISO, UTC
  date: string; // WAT calendar date of kick-off
  status: string;
  finished: boolean;
  home: Team;
  away: Team;
  league: League;
  season: number;
  score: [number, number] | null;
  slug: string;
  tipCount: number; // includes locked tips
  hasFree: boolean;
  hasBanker: boolean;
}

export interface Tip {
  id: string;
  fixtureId: number;
  slot: string;
  legs: Leg[];
  p: number;
  fair: number;
  price: number | null; // 1xBet when published
  capturedAt: string | null;
  isFree: boolean;
  isBanker: boolean;
  result: Result;
  pSource: string | null;
}

export interface Prediction {
  fixtureId: number;
  lambdaHome: number;
  lambdaAway: number;
  probs: Record<string, number>;
  csTop: { score: string; p: number }[];
}

export interface SlipLeg { tip: Tip; fixture: Fixture | undefined; price: number }

export interface Slip {
  kind: string;
  legs: number;
  combinedOdds: number;
  price: number | null;
  capturedAt: string | null;
  chance: number;
  isFree: boolean;
  locksAt: string;
  result: Result;
  selections: SlipLeg[] | null; // null when locked
}

export interface Day {
  window: Window;
  today: string;
  fixtures: Fixture[];
  tips: Map<number, Tip[]>;
  predictions: Map<number, Prediction>;
  slips: Slip[];
  preview: boolean;
  member: boolean;
}

export const FINISHED = new Set(["FT", "AET", "PEN"]);
export const CALLED_OFF = new Set(["PST", "CANC", "ABD", "AWD", "WO"]);
