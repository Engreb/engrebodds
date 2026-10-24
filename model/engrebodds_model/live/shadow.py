"""Weekly shadow evaluation of the live predictions (before anything is published).

Scores the frozen predictions (as they stood at kick-off minus 1 hour) of matches that have finished:
1X2 log loss for p_model and p_final against league base rates and, where a closing price exists, against
de-margined closing odds (power method; Pinnacle, else 1xBet, else Bet365, labelled), plus 1X2 and goals-market
calibration. Written to public.shadow_reports (service role only) and summarised in the run log.
"""

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
from psycopg.types.json import Jsonb

from engrebodds_ingest.config import Settings
from engrebodds_ingest.db import job_run
from engrebodds_model.backtest.metrics import calibration, log_loss
from engrebodds_model.backtest.walkforward import base_rates, outcome_1x2
from engrebodds_model.implied import demargin_power
from engrebodds_model.live import data
from engrebodds_model.markets import OU_LINES, TEAM_LINES

MIN_LEAGUE_MATCHES = 50
CAVEAT = ("Free API plan: few matches have odds snapshots, so market-based p_final and the closing-odds "
          "comparison cover only a small share of matches until the Pro upgrade.")


def _final_1x2(row) -> np.ndarray:
    f = row["final"] or {}
    if all(k in f for k in ("1X2|1", "1X2|X", "1X2|2")):
        return np.array([f["1X2|1"]["p"], f["1X2|X"]["p"], f["1X2|2"]["p"]], dtype=float)
    return np.array([row["p_home"], row["p_draw"], row["p_away"]], dtype=float)


def _goals_calibration(df: pd.DataFrame) -> dict:
    """Calibration error per goals market family, from the stored p_model (unthinned rows only)."""
    rows = df[df["probs"].map(lambda p: bool(p))]
    if rows.empty:
        return {}
    total, hg, ag = (rows.hg + rows.ag).to_numpy(), rows.hg.to_numpy(), rows.ag.to_numpy()
    targets = {f"OU|O|{l:g}": total > l for l in OU_LINES}
    targets["BTTS|Y"] = (hg > 0) & (ag > 0)
    for l in TEAM_LINES:
        targets[f"TEAM_OU|HO|{l:g}"] = hg > l
        targets[f"TEAM_OU|AO|{l:g}"] = ag > l
    out = {}
    for k, hit in targets.items():
        p = rows["probs"].map(lambda d: d.get(k)).to_numpy(dtype=float)
        ok = np.isfinite(p)
        if ok.sum() >= 30:
            ece, _ = calibration(p[ok], hit[ok])
            out[k] = {"n": int(ok.sum()), "calibration_error": round(float(ece), 4),
                      "mean_p": round(float(p[ok].mean()), 4), "hit_rate": round(float(hit[ok].mean()), 4)}
    return out


def score(df: pd.DataFrame, closing: pd.DataFrame, history: pd.DataFrame) -> dict:
    """All metrics for one slice of finished, predicted matches."""
    if df.empty:
        return {"matches": 0}
    y = outcome_1x2(df.hg, df.ag)
    pm = df[["p_home", "p_draw", "p_away"]].to_numpy(dtype=float)
    pf = np.vstack([_final_1x2(r) for _, r in df.iterrows()])
    rates = {}
    for league, g in df.groupby("league"):
        br = base_rates(history[history.league == league], g.kickoff.min())
        rates[league] = [br["h"], br["d"], br["a"]]
    pb = np.clip(np.array([rates[lg] for lg in df.league], dtype=float), 1e-6, 1)   # aligned with df's rows
    out = {
        "matches": int(len(df)),
        "with_market_p_final": int(df["final"].map(lambda f: "1X2|1" in (f or {})).sum()),
        "log_loss_model": round(log_loss(pm, y), 4),
        "log_loss_final": round(log_loss(pf, y), 4),
        "log_loss_base_rates": round(log_loss(pb, y), 4),
        "calibration_1x2_model": round(calibration(pm.ravel(), np.eye(3)[y].ravel())[0], 4),
        "calibration_1x2_final": round(calibration(pf.ravel(), np.eye(3)[y].ravel())[0], 4),
        "data_quality_share_ok": round(float((df.data_quality.astype(float) >= 0.6).mean()), 3),
        "fresh_days_median": float(df.fresh_days.median()),
        "goals_calibration": _goals_calibration(df),
    }
    sub = df.merge(closing, on="fixture_id", how="inner").dropna(subset=["price_h", "price_d", "price_a"])
    if len(sub):
        ys = outcome_1x2(sub.hg, sub.ag)
        pc = np.vstack([demargin_power([r.price_h, r.price_d, r.price_a]) for r in sub.itertuples()])
        out["closing"] = {
            "matches": int(len(sub)), "bookmakers": sub.bookmaker.value_counts().to_dict(),
            "log_loss_model": round(log_loss(sub[["p_home", "p_draw", "p_away"]].to_numpy(dtype=float), ys), 4),
            "log_loss_final": round(log_loss(np.vstack([_final_1x2(r) for _, r in sub.iterrows()]), ys), 4),
            "log_loss_closing": round(log_loss(pc, ys), 4),
        }
    return out


def evaluate(df: pd.DataFrame, closing: pd.DataFrame, history: pd.DataFrame, week_start) -> dict:
    """The weekly report: this week and to date, overall, per league (50+ matches) and per settings version."""
    week_start = pd.Timestamp(week_start)
    report = {"week_start": str(week_start.date()), "caveat": CAVEAT, "sections": {}}
    for name, part in (("week", df[df.kickoff >= week_start]), ("to_date", df)):
        section = {"overall": score(part, closing, history), "by_league": {}, "by_version": {}}
        for league, g in part.groupby("league"):
            if len(g) >= MIN_LEAGUE_MATCHES:
                section["by_league"][g.league_name.iloc[0]] = score(g, closing, history)
        for version, g in part.groupby("model_version"):
            section["by_version"][version] = score(g, closing, history)
        report["sections"][name] = section
    report["league_gate"] = {g.league_name.iloc[0]: league_gate(g, closing) for _, g in df.groupby("league")}
    report["league_gate_candidates"] = sorted(n for n, e in report["league_gate"].items() if e["pass"])
    return report


def run(settings: Settings, days: int = 7, **_) -> None:
    with job_run(settings, "shadow_report") as ctx:
        df = data.frozen_finished(ctx.conn)
        now = datetime.now(UTC).replace(tzinfo=None)
        week_start = now - timedelta(days=days)
        if df.empty:
            ctx.notes.append("no finished matches with predictions yet")
            return
        closing = data.closing_1x2(ctx.conn, df.fixture_id.astype(int).tolist())
        history = data.training(ctx.conn, df.kickoff.min() - timedelta(days=1095), now)
        report = evaluate(df, closing, history, week_start)
        latest = df.sort_values("kickoff").iloc[-1]
        ctx.conn.execute(
            "insert into public.shadow_reports (week_start, settings_id, model_version, results) values (%s, %s, %s, %s)",
            (week_start.date(), None if pd.isna(latest.settings_id) else int(latest.settings_id),
             latest.model_version, Jsonb(report)))
        ctx.rows += 1
        w = report["sections"]["week"]["overall"]
        ctx.notes.append(f"week: {w.get('matches', 0)} matches; log loss model {w.get('log_loss_model')}, "
                         f"final {w.get('log_loss_final')}, base rates {w.get('log_loss_base_rates')}; "
                         f"closing comparison on {w.get('closing', {}).get('matches', 0)} matches")
        ctx.notes.append(f"league gate candidates: {', '.join(report['league_gate_candidates']) or 'none yet'}")
        ctx.notes.append(CAVEAT)


# ---------- league gate: evaluates the product as it would be published ----------

GATE_MIN_MATCHES = 150
GATE_LOGLOSS = 0.01
GATE_SLOT_CALIBRATION = 0.03
GATE_SLOTS = ("result", "goals", "btts")


def league_gate(df: pd.DataFrame, closing: pd.DataFrame) -> dict:
    """Evidence for one league: tips simulated from frozen predictions with the live selection rules.

    Passes with >= 150 matches, 1X2 log loss on p_final within 0.01 of de-margined closing odds, and calibration
    of the selected tips within 3pp for each of result, goals and GG/NG. Prices at freeze time aren't kept, so the
    goals slot's 1.30 threshold uses fair odds here (live uses the 1xBet price where present).
    """
    from engrebodds_model.backtest.products import won
    from engrebodds_model.live.tips import select_tips

    usable = df[df["probs"].map(bool) & df["rho"].notna()]
    out = {"matches": int(len(df)), "simulated": int(len(usable)),
           "model_fallback_share": round(float(1 - df["final"].map(lambda f: "1X2|1" in (f or {})).mean()), 3)
           if len(df) else None}
    picks = {slot: ([], []) for slot in GATE_SLOTS}
    for _, r in usable.iterrows():
        for t in select_tips(r.to_dict(), {}):
            if t["slot"] in picks:
                picks[t["slot"]][0].append(t["p"])
                picks[t["slot"]][1].append(won(t["legs"], int(r.hg), int(r.ag)))
    out["slot_calibration"] = {}
    for slot, (p, hit) in picks.items():
        if len(p) >= 30:
            ece, _ = calibration(np.array(p), np.array(hit, dtype=float))
            out["slot_calibration"][slot] = {"n": len(p), "calibration_error": round(float(ece), 4),
                                             "mean_p": round(float(np.mean(p)), 4), "hit_rate": round(float(np.mean(hit)), 4)}
    sub = df.merge(closing, on="fixture_id", how="inner").dropna(subset=["price_h", "price_d", "price_a"])
    if len(sub):
        ys = outcome_1x2(sub.hg, sub.ag)
        pc = np.vstack([demargin_power([r.price_h, r.price_d, r.price_a]) for r in sub.itertuples()])
        ll_final = log_loss(np.vstack([_final_1x2(r) for _, r in sub.iterrows()]), ys)
        out["closing"] = {"matches": int(len(sub)), "bookmakers": sub.bookmaker.value_counts().to_dict(),
                          "log_loss_final": round(ll_final, 4), "log_loss_closing": round(log_loss(pc, ys), 4),
                          "gap": round(ll_final - log_loss(pc, ys), 4)}
    reasons = []
    if out["matches"] < GATE_MIN_MATCHES:
        reasons.append(f"only {out['matches']} matches (needs {GATE_MIN_MATCHES})")
    if "closing" not in out:
        reasons.append("no closing odds to compare against")
    elif out["closing"]["gap"] > GATE_LOGLOSS:
        reasons.append(f"log loss {out['closing']['gap']:+.4f} vs closing (limit +{GATE_LOGLOSS})")
    for slot in GATE_SLOTS:
        c = out["slot_calibration"].get(slot)
        if c is None:
            reasons.append(f"too few {slot} tips to judge calibration")
        elif c["calibration_error"] > GATE_SLOT_CALIBRATION:
            reasons.append(f"{slot} tips off by {c['calibration_error'] * 100:.1f}pp (limit 3pp)")
    out["pass"] = not reasons
    out["reasons"] = reasons
    return out


def league_evidence(conn, league_id: int) -> dict:
    df = data.frozen_finished(conn)
    df = df[df.league == str(league_id)]
    closing = data.closing_1x2(conn, df.fixture_id.astype(int).tolist()) if len(df) else pd.DataFrame(
        columns=["fixture_id", "bookmaker", "price_h", "price_d", "price_a"])
    return league_gate(df, closing)
