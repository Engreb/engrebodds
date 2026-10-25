import type { APIRoute } from "astro";
import { toCsv } from "../lib/csv";
import { recordRows } from "../lib/record";

// Every graded tip, nothing filtered out.
export const GET: APIRoute = async () => {
  const rows = await recordRows();
  const body = toCsv(
    ["kickoff_utc", "league", "home", "away", "slot", "banker", "free", "selection", "model_prob", "fair_odds", "odds",
     "odds_source", "bookmaker", "ft_home", "ft_away", "result", "profit_units"],
    rows.map((r) => [r.kickoff_at, r.league, r.home, r.away, r.slot, r.is_banker, r.is_free, r.description, r.model_prob,
      r.fair_odds, r.ref_odds, r.ref_odds == null ? "no market price" : "bookmaker", r.ref_bookmaker, r.home_90, r.away_90,
      r.result, r.profit_units]),
  );
  return new Response(body, {
    headers: { "Content-Type": "text/csv; charset=utf-8", "Content-Disposition": 'attachment; filename="engrebodds-track-record.csv"' },
  });
};
