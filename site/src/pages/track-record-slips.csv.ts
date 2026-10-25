import type { APIRoute } from "astro";
import { toCsv } from "../lib/csv";
import { slipRows } from "../lib/record";

// Every graded slip, nothing filtered out.
export const GET: APIRoute = async () => {
  const rows = await slipRows();
  const body = toCsv(
    ["slip_date", "kind", "legs", "odds_1xbet", "chance_all_land", "settled_odds", "result", "profit_units"],
    rows.map((r) => [r.slip_date, r.kind, r.legs, r.ref_odds, r.combined_prob, r.settled_odds, r.result, r.profit_units]),
  );
  return new Response(body, {
    headers: { "Content-Type": "text/csv; charset=utf-8", "Content-Disposition": 'attachment; filename="engrebodds-slips.csv"' },
  });
};
