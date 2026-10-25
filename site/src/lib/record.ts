// Track record reads (public views over graded tips only).
import { publicClient } from "./db";

export interface Summary {
  breakdown: string; label: string; settled: number; won: number; void: number; hit_rate: number | null;
  priced: number; no_market_price: number; avg_odds: number | null; units: number | null; roi: number | null;
}

export async function summaries(): Promise<Summary[]> {
  const { data, error } = await publicClient().from("track_record_summary").select("*");
  if (error) throw new Error(`track_record_summary: ${error.message}`);
  return (data as Summary[]).map((r) => ({
    ...r, hit_rate: r.hit_rate == null ? null : Number(r.hit_rate), avg_odds: r.avg_odds == null ? null : Number(r.avg_odds),
    units: r.units == null ? null : Number(r.units), roi: r.roi == null ? null : Number(r.roi),
  }));
}

export const overall = (rows: Summary[]) => rows.find((r) => r.breakdown === "all");

export interface RecordRow {
  tip_id: number; kickoff_at: string; league: string; home: string; away: string; slot: string; is_banker: boolean;
  is_free: boolean; description: string; model_prob: number; ref_odds: number | null; ref_bookmaker: string | null;
  fair_odds: number; home_90: number | null; away_90: number | null; result: string; profit_units: number | null;
}

const PAGE = 1000; // the API's row cap per request

/** Graded tips, newest first. Without a limit, every row (paged): the record is never trimmed. */
export async function recordRows(opts: { bankers?: boolean; limit?: number } = {}): Promise<RecordRow[]> {
  const out: RecordRow[] = [];
  for (let from = 0; ; from += PAGE) {
    let q = publicClient().from("track_record").select("*").order("kickoff_at", { ascending: false }).order("tip_id");
    if (opts.bankers) q = q.eq("is_banker", true);
    const to = opts.limit ? Math.min(opts.limit, from + PAGE) - 1 : from + PAGE - 1;
    const { data, error } = await q.range(from, to);
    if (error) throw new Error(`track_record: ${error.message}`);
    out.push(...(data as RecordRow[]));
    if (data.length < to - from + 1 || (opts.limit && out.length >= opts.limit)) return out;
  }
}

export interface SlipRow { slip_id: number; kind: string; slip_date: string; legs: number; ref_odds: number | null;
                           combined_prob: number; settled_odds: number | null; result: string; profit_units: number | null }

export async function slipRows(): Promise<SlipRow[]> {
  const out: SlipRow[] = [];
  for (let from = 0; ; from += PAGE) {
    const { data, error } = await publicClient().from("slip_record").select("*")
      .order("slip_date", { ascending: false }).order("slip_id").range(from, from + PAGE - 1);
    if (error) throw new Error(`slip_record: ${error.message}`);
    out.push(...(data as SlipRow[]));
    if (data.length < PAGE) return out;
  }
}

export interface SlipSummary { kind: string; settled: number; won: number; hit_rate: number | null; avg_price: number | null;
                               avg_chance_all_land: number | null; units: number | null; roi: number | null }

export async function slipSummaries(): Promise<SlipSummary[]> {
  const { data, error } = await publicClient().from("slip_record_summary").select("*");
  if (error) throw new Error(`slip_record_summary: ${error.message}`);
  return data as SlipSummary[];
}
