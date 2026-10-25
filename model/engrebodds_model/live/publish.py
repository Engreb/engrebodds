"""Publish the most-likely product for the current publishing day, behind the PUBLISH_TIPS switch.

PUBLISH_TIPS unset or anything but "on": PREVIEW mode, which replaces public.tip_previews and never touches
the track record. PUBLISH_TIPS=on: tips, legs and slips go into the permanent record (never edited or deleted).

The window is the publishing day containing the run (06:45-06:45 UTC). Only fixtures in it, at least 75 minutes
from kick-off and without tips yet, are considered. Slips are built only in the window's first run, from the whole
day's priced tips. `preview_all` ignores league eligibility and quality filters (preview only), to show real output.
"""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pandas as pd
from psycopg.types.json import Jsonb

from engrebodds_ingest.config import Settings
from engrebodds_ingest.db import job_run
from engrebodds_model.live import proof
from engrebodds_model.live import settings as live_settings
from engrebodds_model.live.tips import Price, build_slips, choose_banker, choose_free, publishing_window, select_tips

LEAD = timedelta(minutes=75)
MIN_QUALITY = 0.6
MAX_FRESH_DAYS = 45
NPFL_API_ID = 399

CANDIDATES_SQL = """
select f.id as fixture_id, (f.kickoff_at at time zone 'UTC') as kickoff, l.id as league_id, l.api_league_id,
       l.name as league_name, l.country, l.tier, l.tips_enabled, l.display_order as priority,
       ht.name as home, aw.name as away,
       p.id as prediction_id, p.lambda_home, p.lambda_away, p.rho, p.probs, p.final, p.data_quality, p.fresh_days
  from public.fixtures f
  join public.leagues l on l.id = f.league_id
  join public.teams ht on ht.id = f.home_team_id
  join public.teams aw on aw.id = f.away_team_id
  join public.predictions p on p.fixture_id = f.id and p.model_version = %(version)s
 where f.status in ('NS', 'TBD')
   and f.kickoff_at >= %(earliest)s and f.kickoff_at >= %(start)s and f.kickoff_at < %(end)s
   and p.rho is not null and p.probs <> '{}'::jsonb
   and not exists (select 1 from public.tips t where t.fixture_id = f.id and t.product = 'likely')
 order by f.kickoff_at
"""

PRICES_SQL = """
select o.fixture_id, o.market_code, o.selection, o.line, o.price, o.captured_at, o.bookmaker_id
  from public.odds_latest o
  join public.bookmakers b on b.id = o.bookmaker_id and b.is_display
 where o.fixture_id = any(%(ids)s)
"""


def eligible(row) -> tuple[bool, str]:
    if row.country == "World":
        return False, "continental competition"
    if row.api_league_id == NPFL_API_ID:
        return False, "NPFL (no tips yet)"
    if not row.tips_enabled:
        return False, "league not enabled"
    if float(row.data_quality) < MIN_QUALITY:
        return False, "data quality below 0.6"
    if row.fresh_days is None or row.fresh_days > MAX_FRESH_DAYS:
        return False, "stale ratings"
    return True, ""


def load_prices(conn, ids: list[int]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    if ids:
        for fid, m, s, line, price, at, book in conn.execute(PRICES_SQL, {"ids": ids}).fetchall():
            out.setdefault(fid, {})[(m, s, None if line is None else float(line))] = Price(float(price), at, book)
    return out


def plan(conn, now: datetime, preview_all: bool) -> dict:
    """What this run would publish: per-fixture tips, free fixtures and the banker (no writes)."""
    start, end = publishing_window(now)
    ls = live_settings.active(conn)
    with conn.cursor() as cur:
        cur.execute(CANDIDATES_SQL, {"version": ls.version, "earliest": now + LEAD, "start": start, "end": end})
        cols = [d.name for d in cur.description]
        cands = pd.DataFrame(cur.fetchall(), columns=cols)
    skipped: dict[str, int] = {}
    chosen = []
    for r in cands.itertuples():
        ok, why = eligible(r)
        if ok or preview_all:
            chosen.append(r)
        else:
            skipped[why] = skipped.get(why, 0) + 1
    prices = load_prices(conn, [int(r.fixture_id) for r in chosen])
    fixtures = []
    for r in chosen:
        pred = {"probs": r.probs, "final": r.final, "lambda_home": r.lambda_home, "lambda_away": r.lambda_away,
                "rho": r.rho, "data_quality": r.data_quality}
        tips = select_tips(pred, prices.get(int(r.fixture_id), {}))
        locks = r.kickoff.to_pydatetime() - timedelta(hours=1)
        for t in tips:
            t.update({"fixture_id": int(r.fixture_id), "locks_at": locks})
        fixtures.append({"row": r, "tips": tips})
    return {"window": (start, end), "settings": ls, "fixtures": fixtures, "skipped": skipped}


def run(settings: Settings, preview_all: bool = False, now: datetime | None = None, **_) -> None:
    mode = "on" if os.environ.get("PUBLISH_TIPS", "").strip().lower() == "on" and not preview_all else "preview"
    with job_run(settings, "publish") as ctx:
        now = now or datetime.now(UTC).replace(tzinfo=None)
        p = plan(ctx.conn, now, preview_all)
        start, end = p["window"]
        ctx.notes.append(f"mode {mode}{' (all leagues, filters ignored)' if preview_all else ''}; window "
                         f"{start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} UTC; settings {p['settings'].version}")
        ctx.notes.append(f"{len(p['fixtures'])} fixtures to tip; skipped: {p['skipped'] or 'none'}")
        if mode == "on":
            ctx.rows += _publish(ctx.conn, p, now, ctx.notes)
        else:
            ctx.rows += _preview(ctx.conn, p, ctx.notes)
    if mode == "on":
        # The tips are committed now: post the run's hash publicly (retried by the proof job until kick-off).
        proof.run(settings)

def _free_and_banker(conn, p: dict, mode_on: bool) -> tuple[set, dict | None]:
    start, end = p["window"]
    already_free, banker_exists = 0, False
    if mode_on:
        already_free = conn.execute(
            "select count(distinct fixture_id) from public.tips where is_free and kickoff_at >= %s and kickoff_at < %s",
            (start, end)).fetchone()[0]
        banker_exists = conn.execute(
            "select exists (select 1 from public.tips where is_banker and kickoff_at >= %s and kickoff_at < %s)",
            (start, end)).fetchone()[0]
    cands = [{"fixture_id": int(f["row"].fixture_id), "priority": None if pd.isna(f["row"].priority) else int(f["row"].priority),
              "kickoff": f["row"].kickoff} for f in p["fixtures"]]
    free = set(choose_free(cands, already_free))
    banker = None if banker_exists else choose_banker([t for f in p["fixtures"] for t in f["tips"]])
    return free, banker


def _preview(conn, p: dict, notes: list) -> int:
    run_id = uuid.uuid4().hex[:12]
    free, banker = _free_and_banker(conn, p, mode_on=False)
    conn.execute("delete from public.tip_previews")
    rows = []
    for f in p["fixtures"]:
        r = f["row"]
        payload = {"league": r.league_name, "match": f"{r.home} v {r.away}", "kickoff_utc": r.kickoff.isoformat(),
                   "free": int(r.fixture_id) in free, "data_quality": float(r.data_quality), "fresh_days": r.fresh_days,
                   "tips": [_tip_payload(t, banker) for t in f["tips"]]}
        rows.append((run_id, p["window"][0], "tip", int(r.fixture_id), Jsonb(payload)))
    slips = build_slips([t for f in p["fixtures"] for t in f["tips"]])
    for s in slips:
        rows.append((run_id, p["window"][0], "slip", None, Jsonb(_slip_payload(s))))
    with conn.cursor() as cur:
        cur.executemany("insert into public.tip_previews (run_id, window_start, kind, fixture_id, payload) "
                        "values (%s, %s, %s, %s, %s)", rows)
    n_tips = sum(len(f["tips"]) for f in p["fixtures"])
    priced = sum(1 for f in p["fixtures"] for t in f["tips"] if t["price"])
    notes.append(f"preview: {n_tips} tips ({priced} with a 1xBet price), {len(free)} free fixtures, "
                 f"banker {'chosen' if banker else 'none'}, {len(slips)} slips; previous preview replaced")
    return len(rows)


def _publish(conn, p: dict, now: datetime, notes: list) -> int:
    free, banker = _free_and_banker(conn, p, mode_on=True)
    written = 0
    for f in p["fixtures"]:
        r = f["row"]
        for t in f["tips"]:
            tip_id = conn.execute(
                """insert into public.tips (fixture_id, prediction_id, product, slot, is_banker, confidence, model_prob,
                       p_source, fair_odds, ref_odds, ref_bookmaker_id, ref_captured_at, is_free, kickoff_at)
                   values (%s, %s, 'likely', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
                (int(r.fixture_id), int(r.prediction_id), t["slot"], t is banker, t["confidence"], round(t["p"], 4),
                 t["p_source"], t["fair"], t["price"], t["bookmaker_id"], t["captured_at"], int(r.fixture_id) in free,
                 r.kickoff.to_pydatetime())).fetchone()[0]
            for n, (m, s, line) in enumerate(t["legs"], start=1):
                conn.execute("insert into public.tip_legs (tip_id, leg_no, market_code, selection, line) "
                             "values (%s, %s, %s, %s, %s)", (tip_id, n, m, s, line))
            written += 1
    start, _ = p["window"]
    slips_made = 0
    if not conn.execute("select exists (select 1 from public.slips where slip_date = %s)", (start.date(),)).fetchone()[0]:
        slips_made = _publish_slips(conn, p, now)
    run_proof = proof.record_run(conn, start.date())
    notes.append(f"published {written} tips on {len(p['fixtures'])} fixtures; {len(free)} free fixtures; "
                 f"banker {'set' if banker else 'unchanged'}; {slips_made} slips"
                 + (f"; proof sha256 {run_proof['sha256'][:12]}…" if run_proof else ""))
    return written + slips_made


DAY_TIPS_SQL = """
select t.id, t.fixture_id, t.slot, t.model_prob, t.ref_odds, t.ref_captured_at, t.locks_at,
       l.market_code, l.selection, l.line
  from public.tips t
  join public.tip_legs l on l.tip_id = t.id
 where t.product = 'likely' and t.ref_odds is not null and t.locks_at > %(now)s
   and t.kickoff_at >= %(start)s and t.kickoff_at < %(end)s
   and (select count(*) from public.tip_legs x where x.tip_id = t.id) = 1
"""


def _publish_slips(conn, p: dict, now: datetime) -> int:
    start, end = p["window"]
    day = [{"tip_id": tid, "fixture_id": fid, "slot": slot, "p": float(prob), "price": float(odds),
            "captured_at": cap, "locks_at": locks, "legs": [(m, s, None if line is None else float(line))]}
           for tid, fid, slot, prob, odds, cap, locks, m, s, line in conn.execute(
               DAY_TIPS_SQL, {"now": now, "start": start, "end": end}).fetchall()]
    made = 0
    for s in build_slips(day):
        slip_id = conn.execute(
            """insert into public.slips (kind, slip_date, target_odds, combined_odds, combined_prob, locks_at,
                   ref_odds, ref_captured_at) values (%s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (s["kind"], start.date(), s["target"], s["price"], round(s["chance"], 5), s["locks_at"],
             s["price"], s["captured_at"])).fetchone()[0]
        for t in s["legs"]:
            conn.execute("insert into public.slip_legs (slip_id, tip_id, odds) values (%s, %s, %s)",
                         (slip_id, t["tip_id"], t["price"]))
        made += 1
    return made


def _tip_payload(t: dict, banker: dict | None) -> dict:
    return {"slot": t["slot"], "tip": " & ".join(f"{m} {s}" + ("" if l is None else f" {l:g}") for m, s, l in t["legs"]),
            "p": round(t["p"], 4), "p_source": t["p_source"], "fair": t["fair"], "confidence": t["confidence"],
            "price_1xbet": t["price"], "captured_at": t["captured_at"].isoformat() if t["captured_at"] else None,
            "banker": t is banker, "market_price": t["price"] is not None}


def _slip_payload(s: dict) -> dict:
    return {"kind": s["kind"], "target": s["target"], "price_1xbet": s["price"], "chance_all_land": round(s["chance"], 4),
            "captured_at": s["captured_at"].isoformat(), "locks_at": s["locks_at"].isoformat(),
            "legs": [{"fixture_id": t["fixture_id"], "slot": t["slot"], "p": round(t["p"], 4), "price_1xbet": t["price"]}
                     for t in s["legs"]]}
