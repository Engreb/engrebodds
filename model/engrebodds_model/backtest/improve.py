"""Model improvement ladder (docs/model-improvement-plan.md). Private football-data research only.

Experiments are judged by rolling validation (validation.py) and accepted only on out-of-sample evidence:
  1 linked divisions + new-team prior   2 season-boundary reset   3 goals calibration + draw shift
  4 per-league tuning (needs > 0.001)   5 shots on target (only if the shots data check passed)
Then the accepted model, the first backtest's model and experiment 0 (market probabilities for the
most-likely product) are compared on the 2023/24-2024/25 holdout (gate), 2025/26, and 2026/27 to date
(comparison only; benchmark = market-average closing).
"""

import json
from dataclasses import asdict, replace
from datetime import date

import numpy as np
import pandas as pd

from engrebodds_model.backtest import football_data as fd
from engrebodds_model.backtest import validation as val
from engrebodds_model.backtest.metrics import calibration, demargin, log_loss
from engrebodds_model.backtest.products import ProductConfig, simulate, slips, summarise
from engrebodds_model.backtest.run import (
    EDGE_GRID, FINAL, HOLD, bench_1x2, league_gate, product_tables, slip_table, value_eligibility,
)
from engrebodds_model.backtest.walkforward import Config, outcome_1x2

GRID = [(xi, pen) for xi in (0.0015, 0.003) for pen in (1.0, 3.0, 8.0)]
FIRST_BACKTEST = Config(xi=0.002, penalty=2.0)
CURRENT_RANGE = ("2026-07-01", "2027-07-01")
SHOTS_CHECK = fd.REPO_ROOT / "ingest" / "data" / "shots_check.json"


def family(**kw) -> dict[str, Config]:
    return {c.key(): c for c in (Config(xi=xi, penalty=pen, **kw) for xi, pen in GRID)}


CONFIGS: dict[str, Config] = {}       # every config predicted so far, by key


def predict(leagues, cfgs: dict[str, Config], workers, rng=val.PRED_RANGE) -> dict[str, pd.DataFrame]:
    out = {}
    CONFIGS.update(cfgs)
    for k, cfg in cfgs.items():
        print(f"  predictions {k} {rng[0]}..{rng[1]}", flush=True)
        out[k] = val.cached_predictions(leagues, cfg, *rng, workers)
    return out


def coverage(preds: dict[str, pd.DataFrame]) -> int:
    df = next(iter(preds.values()))
    return int(((df.dq >= val.MIN_QUALITY) & df.season.isin(val.VALIDATION)).sum())


def step(name, current, candidate, score=val.ll_1x2, threshold=0.0) -> dict:
    keys = val.common_keys(list(current.values()) + list(candidate.values()))
    res = val.compare(val.rolling(current, keys, score), val.rolling(candidate, keys, score), threshold)
    res.update({"name": name, "matches_compared": len(keys),
                "coverage_before": coverage(current), "coverage_after": coverage(candidate)})
    verdict = "ACCEPTED" if res["accepted"] else "rejected"
    print(f"  {name}: mean {res['mean']:+.5f}, better in {res['wins']}/3 -> {verdict}", flush=True)
    return res


def best_overall(preds: dict[str, pd.DataFrame]) -> str:
    """The config with the lowest 1X2 log loss over every pre-holdout season scored (2017/18-2022/23)."""
    scores = {k: val.ll_1x2(p[(p.dq >= val.MIN_QUALITY) & (p.season >= val.FIRST_SCORED)]) for k, p in preds.items()}
    return min(scores, key=scores.get)


def tip_calibration(tips: pd.DataFrame) -> dict:
    """Per most-likely slot: predicted vs actual by probability band (the overconfidence check)."""
    out = {}
    bands = [0, 0.55, 0.65, 0.75, 0.85, 1.0]
    for slot, g in tips.query("product == 'likely'").groupby("slot"):
        g = g.assign(band=pd.cut(g.p, bands))
        out[slot] = [{"band": str(b), "n": int(len(x)), "p": float(x.p.mean()), "hit": float(x.won.mean())}
                     for b, x in g.groupby("band", observed=True)]
        out[slot].append({"band": "all", "n": int(len(g)), "p": float(g.p.mean()), "hit": float(g.won.mean())})
    return out


def market_gate(pred: pd.DataFrame, leagues) -> pd.DataFrame:
    """Experiment 0's 1X2 probabilities (early market) through the same gate as the model."""
    swapped = pred.copy()
    mp = demargin(pred.ps_h, pred.ps_d, pred.ps_a)
    swapped["p_h"], swapped["p_d"], swapped["p_a"] = mp[:, 0], mp[:, 1], mp[:, 2]
    return league_gate(swapped.dropna(subset=["p_h"]), "psc", leagues)


def choose_edge(pred: pd.DataFrame, calibrator) -> float:
    edges = {}
    for edge in EDGE_GRID:
        s = summarise(simulate(pred, ProductConfig(edge=edge), "psc", calibrator).query("product == 'value'"))
        edges[edge] = s
    usable = {e: s for e, s in edges.items() if s.get("clv_n", 0) >= 300}
    return max(usable, key=lambda e: usable[e]["clv_lo"]) if usable else 0.03


def run(workers=None) -> None:
    failed = fd.download(seasons=[fd.CURRENT])          # current season only, missing files only
    if failed:
        print("current-season download failures:", failed)
    leagues = {c: fd.load(c) for c in fd.LEAGUES}
    leagues_now = {c: fd.load(c, fd.SEASONS + [fd.CURRENT]) for c in fd.LEAGUES}
    out_dir = fd.REPO_ROOT / "reports" / f"backtest-{date.today().isoformat()}-improve"
    out_dir.mkdir(parents=True, exist_ok=True)
    ladder = []

    print("Baseline family (single-league fits)", flush=True)
    current, current_kw = predict(leagues, family(), workers), {}

    print("Experiment 1: linked divisions + new-team prior", flush=True)
    cand = predict(leagues, family(link_divisions=True, new_team_prior=True), workers)
    ladder.append(step("1. Linked divisions + new-team prior", current, cand))
    if ladder[-1]["accepted"]:
        current, current_kw = cand, {"link_divisions": True, "new_team_prior": True}

    print("Experiment 2: season-boundary reset", flush=True)
    cand = {}
    for k in (0.5, 0.8):
        cand.update(predict(leagues, family(**current_kw, season_reset=k), workers))
    ladder.append(step("2. Season-boundary reset (k = 0.5 or 0.8)", current, cand))
    if ladder[-1]["accepted"]:
        current = cand

    print("Experiment 3: goals-market calibration and draw shift", flush=True)
    keys = val.common_keys(list(current.values()))
    chosen_by_season = {r["season"]: current[r["chosen"]] for r in val.rolling(current, keys, val.ll_1x2)}
    cal_res = val.rolling_calibration(chosen_by_season, keys)
    for part, label in (("goals", "3a. Goals-market calibration"), ("draw", "3b. Draw shift (1X2)")):
        r = cal_res[part]
        r.update({"name": label, "matches_compared": len(keys)})
        ladder.append(r)
        print(f"  {label}: mean {r['mean']:+.5f}, better in {r['wins']}/3 -> {'ACCEPTED' if r['accepted'] else 'rejected'}")
    use_goals_cal, use_draw = cal_res["goals"]["accepted"], cal_res["draw"]["accepted"]

    print("Experiment 4: per-league tuning", flush=True)
    keys = val.common_keys(list(current.values()))
    r4 = val.compare(val.rolling(current, keys, val.ll_1x2), val.rolling_per_league(current, keys), threshold=0.001)
    r4.update({"name": "4. Per-league tuning (needs > 0.001)", "matches_compared": len(keys)})
    ladder.append(r4)
    print(f"  per-league: mean {r4['mean']:+.5f}, better in {r4['wins']}/3 -> {'ACCEPTED' if r4['accepted'] else 'rejected'}")

    shots = json.loads(SHOTS_CHECK.read_text()) if SHOTS_CHECK.exists() else {"agree": False}
    if shots.get("agree"):
        print("Experiment 5: shots on target", flush=True)
        # Same structure as the current model (including its season reset, if accepted), plus shots.
        reset = CONFIGS[best_overall(current)].season_reset
        cand = {}
        for s in (0.3, 0.5):
            cand.update(predict(leagues, family(**current_kw, season_reset=reset, shots_weight=s), workers))
        ladder.append(step("5. Shots on target (s = 0.3 or 0.5)", current, cand))
        if ladder[-1]["accepted"]:
            current = cand
    else:
        ladder.append({"name": "5. Shots on target", "skipped": "shots data check did not pass"})

    # ---------- final model ----------
    final_key = best_overall(current)
    final_cfg = CONFIGS[final_key]
    pre = current[final_key]
    pre = pre[(pre.dq >= val.MIN_QUALITY) & (pre.season >= val.FIRST_SCORED)]
    calibrator = None
    if use_goals_cal or use_draw:
        fitted = val.fit_calibrator(pre, draw=use_draw)
        calibrator = replace(fitted, maps=fitted.maps if use_goals_cal else {})
    pcfg = ProductConfig(edge=choose_edge(pre, calibrator))
    print(f"Final model: {final_key}; calibration: goals={use_goals_cal} draw={use_draw}; edge {pcfg.edge}", flush=True)

    print("Holdout 2023/24-2024/25", flush=True)
    hold_new = val.cached_predictions(leagues, final_cfg, *HOLD, workers)
    hold_old = val.cached_predictions(leagues, FIRST_BACKTEST, *HOLD, workers)
    q = lambda p: p[p.dq >= val.MIN_QUALITY]
    hold_new, hold_old = q(hold_new), q(hold_old)
    keys = val.common_keys([hold_new, hold_old])
    common_new, common_old = val.restrict(hold_new, keys), val.restrict(hold_old, keys)

    variants = {
        "First backtest model": (hold_old, None, "model"),
        "Improved model": (hold_new, calibrator, "model"),
        "Experiment 0: market (1X2, O/U 2.5) + model": (hold_new, calibrator, "market"),
        "Experiment 0 variant: market-implied goals": (hold_new, calibrator, "implied"),
    }
    products, tipcal, tips_by = {}, {}, {}
    for name, (pred, cal, source) in variants.items():
        print(f"  products: {name}", flush=True)
        t = simulate(pred, pcfg, "psc", cal, source)
        tips_by[name] = t
        products[name] = product_tables(t)
        tipcal[name] = tip_calibration(t)
    gates = {"First backtest model": league_gate(hold_old, "psc", leagues),
             "Improved model": league_gate(hold_new, "psc", leagues),
             "Experiment 0 (early market, 1X2)": market_gate(hold_new.dropna(subset=["ps_h"]), leagues)}
    eligibility = value_eligibility(tips_by["Improved model"])
    eligible = sorted(set(eligibility.query("eligible").league) & set(gates["Improved model"].query("gate").league))
    priced = val.keyed(common_new.dropna(subset=["psc_h", "psc_d", "psc_a"]))
    cn, co = common_new[val.keyed(common_new).isin(priced)], common_old[val.keyed(common_old).isin(priced)]
    same_matches = {"matches": len(cn), "first_model": val.ll_1x2(co), "improved": val.ll_1x2(cn),
                    "pinnacle_close": log_loss(bench_1x2(cn, "psc"), outcome_1x2(cn.hg, cn.ag)),
                    "coverage_first": len(hold_old), "coverage_improved": len(hold_new),
                    "early_season_first": int(hold_old.date.dt.month.isin([7, 8, 9]).sum()),
                    "early_season_improved": int(hold_new.date.dt.month.isin([7, 8, 9]).sum())}

    print("Final holdout 2025/26 and 2026/27 to date (comparison only)", flush=True)
    later = {}
    for label, rng, data in (("2025/26", FINAL, leagues), ("2026/27 to date", CURRENT_RANGE, leagues_now)):
        new = q(val.cached_predictions(data, final_cfg, *rng, workers))
        old = q(val.cached_predictions(data, FIRST_BACKTEST, *rng, workers))
        k2 = val.common_keys([new, old])
        n2, o2 = val.restrict(new, k2), val.restrict(old, k2)
        n2 = n2.dropna(subset=["avgc_h", "avgc_d", "avgc_a"])
        o2 = o2.dropna(subset=["avgc_h", "avgc_d", "avgc_a"])
        yy = outcome_1x2(n2.hg, n2.ag)
        later[label] = {"matches": len(n2), "first_model": val.ll_1x2(o2), "improved": val.ll_1x2(n2),
                        "market_avg_close": log_loss(bench_1x2(n2, "avgc"), yy),
                        "calibration_first": calibration(o2[["p_h", "p_d", "p_a"]].to_numpy().ravel(),
                                                          np.eye(3)[outcome_1x2(o2.hg, o2.ag)].ravel())[0],
                        "calibration_improved": calibration(n2[["p_h", "p_d", "p_a"]].to_numpy().ravel(),
                                                             np.eye(3)[yy].ravel())[0]}
        if label == "2025/26":
            fv = value_eligibility(simulate(new, pcfg, "avgc", calibrator))
            later[label]["value_kept"] = sorted(l for l in eligible if not fv.empty
                                                and fv.set_index("league")["clv"].get(l, -1) > 0)

    results = {"final_config": asdict(final_cfg), "calibration": {"goals": use_goals_cal, "draw": use_draw},
               "edge": pcfg.edge, "ladder": ladder, "holdout_same_matches": same_matches,
               "products": products, "tip_calibration": tipcal, "value_eligible": eligible, "later": later,
               "slips_improved": slip_table(slips(tips_by["Improved model"])),
               "slips_market": slip_table(slips(tips_by["Experiment 0: market (1X2, O/U 2.5) + model"]))}
    for name, g in gates.items():
        g.to_csv(out_dir / f"gate-{name.split(' ')[0].lower()}.csv", index=False)
    eligibility.to_csv(out_dir / "value_eligibility.csv", index=False)
    (out_dir / "results.json").write_text(json.dumps(results, indent=2, default=lambda o: float(o)
                                                     if isinstance(o, (np.floating, np.integer)) else str(o)),
                                          encoding="utf-8")
    from engrebodds_model.backtest.report import write_improvement_report
    write_improvement_report(out_dir, results, gates, eligibility)
    print(f"Report: {out_dir / 'report.md'}", flush=True)
