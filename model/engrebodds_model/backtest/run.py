"""Run the full backtest and write the report (private use only, see package docstring).

1. Tune on 2019/20-2022/23: time decay and penalty (1X2 log loss), half-time share (per league vs global),
   blend weight (model vs early Pinnacle), value edge (best lower bound of mean CLV).
2. Holdout 2023/24-2024/25, evaluated once with the tuned settings: league gate, value eligibility,
   both products, slips.
3. Final holdout 2025/26 with everything frozen: benchmark is the market-average closing price
   (Pinnacle unreliable since 23 Jul 2025); it can only remove value leagues.
"""

import json
from dataclasses import asdict, replace
from datetime import date
from itertools import product as grid
from pathlib import Path

import numpy as np
import pandas as pd

from engrebodds_model.backtest import football_data as fd
from engrebodds_model.backtest.metrics import calibration, demargin, log_loss
from engrebodds_model.backtest.products import ProductConfig, simulate, slips, summarise
from engrebodds_model.backtest.walkforward import Config, base_rates, outcome_1x2, walk_forward_all
from engrebodds_model.markets import ht_matrix, prob

TUNE = ("2019-07-01", "2023-07-01")
HOLD = ("2023-07-01", "2025-07-01")
FINAL = ("2025-07-01", "2026-07-01")

XI_GRID = (0.0, 0.001, 0.002, 0.004)
PENALTY_GRID = (0.0, 2.0, 8.0, 20.0)
BLEND_GRID = tuple(np.round(np.linspace(0, 1, 11), 2))
EDGE_GRID = (0.01, 0.02, 0.03, 0.05, 0.08, 0.12)

GATE_LOGLOSS = 0.01        # model 1X2 log loss at most this much worse than the benchmark
GATE_ECE = 0.03            # calibration error at most 3 percentage points
VALUE_MIN_TIPS = 150
QUICK_LEAGUES = ("E0", "D1", "SP1", "I1", "F1")


def _quality(pred: pd.DataFrame, min_quality: float = 0.6) -> pd.DataFrame:
    return pred[pred["dq"] >= min_quality]


def model_1x2(pred: pd.DataFrame) -> np.ndarray:
    return pred[["p_h", "p_d", "p_a"]].to_numpy()


def bench_1x2(pred: pd.DataFrame, prefix: str) -> np.ndarray:
    return demargin(pred[f"{prefix}_h"], pred[f"{prefix}_d"], pred[f"{prefix}_a"])


def blend_1x2(pred: pd.DataFrame, w: float, early: str) -> np.ndarray:
    return w * model_1x2(pred) + (1 - w) * bench_1x2(pred, early)


def league_gate(pred: pd.DataFrame, benchmark: str, leagues: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Per league: model vs benchmark 1X2 log loss on the same matches, base-rate log loss, calibration."""
    out = []
    for code, g in pred.groupby("league"):
        g = g.dropna(subset=[f"{benchmark}_h", f"{benchmark}_d", f"{benchmark}_a"])
        if g.empty:
            continue
        y = outcome_1x2(g.hg, g.ag)
        pm = model_1x2(g)
        br = base_rates(leagues[code], g.date.min())
        pb = np.tile([br["h"], br["d"], br["a"]], (len(g), 1))
        ece, _ = calibration(pm.ravel(), (np.eye(3)[y]).ravel())
        ll_model, ll_bench = log_loss(pm, y), log_loss(bench_1x2(g, benchmark), y)
        out.append({"league": code, "name": fd.LEAGUES[code], "matches": len(g),
                    "ll_model": ll_model, "ll_bench": ll_bench, "ll_base": log_loss(pb, y),
                    "ll_gap": ll_model - ll_bench, "ece": ece,
                    "gate": bool(ll_model - ll_bench <= GATE_LOGLOSS and ece <= GATE_ECE)})
    return pd.DataFrame(out)


def tune(leagues, workers) -> tuple[Config, dict]:
    notes: dict = {"grid": []}
    best, best_ll, preds = None, np.inf, {}
    for xi, pen in grid(XI_GRID, PENALTY_GRID):
        cfg = Config(xi=xi, penalty=pen)
        pred = _quality(walk_forward_all(leagues, *TUNE, cfg, workers))
        ll = log_loss(model_1x2(pred), outcome_1x2(pred.hg, pred.ag))
        notes["grid"].append({"xi": xi, "penalty": pen, "matches": len(pred), "ll_1x2": ll})
        print(f"  tune xi={xi} penalty={pen}: 1X2 log loss {ll:.4f} on {len(pred)} matches", flush=True)
        if ll < best_ll:
            best, best_ll, preds = cfg, ll, pred
    pred = preds

    # Half-time share: per league (estimated from each training window) vs one global share.
    ht_ok = pred.dropna(subset=["hthg", "htag"])
    y_ht = outcome_1x2(ht_ok.hthg, ht_ok.htag)
    ll_league = log_loss(ht_ok[["p_ht_h", "p_ht_d", "p_ht_a"]].to_numpy(), y_ht)
    share = float(ht_ok["ht_share"].mean())
    global_probs = np.array([[prob(m, "HT_1X2", s) for s in ("1", "X", "2")]
                             for m in (ht_matrix(l, u, share) for l, u in zip(ht_ok.lam, ht_ok.mu))])
    ll_global = log_loss(global_probs, y_ht)
    notes["ht_share"] = {"per_league_ll": ll_league, "global_ll": ll_global, "global_share": share,
                         "chosen": "per league" if ll_league <= ll_global else f"global {share:.3f}"}
    if ll_global < ll_league:
        best = replace(best, ht_share=share)

    # Blend weight w (1 = model only) against early Pinnacle, on matches with those prices.
    blend_rows = pred.dropna(subset=["ps_h", "ps_d", "ps_a"])
    y = outcome_1x2(blend_rows.hg, blend_rows.ag)
    blend_ll = {float(w): log_loss(blend_1x2(blend_rows, w, "ps"), y) for w in BLEND_GRID}
    notes["blend"] = {"ll_by_w": blend_ll, "best_w": min(blend_ll, key=blend_ll.get),
                      "ll_pinnacle_early": log_loss(bench_1x2(blend_rows, "ps"), y)}

    # Value edge: the threshold whose value tips have the best lower bound on mean CLV.
    edges = {}
    for edge in EDGE_GRID:
        s = summarise(simulate(pred, ProductConfig(edge=edge), "psc").query("product == 'value'"))
        edges[edge] = s
        print(f"  tune edge={edge}: {s.get('tips', 0)} value tips, mean CLV {s.get('clv', np.nan):+.4f}", flush=True)
    usable = {e: s for e, s in edges.items() if s.get("clv_n", 0) >= 300}
    notes["edge"] = {str(e): {k: s.get(k) for k in ("tips", "clv", "clv_lo", "clv_hi", "roi")} for e, s in edges.items()}
    notes["chosen_edge"] = max(usable, key=lambda e: usable[e]["clv_lo"]) if usable else 0.03
    notes["chosen"] = asdict(best)
    return best, notes


def product_tables(tips: pd.DataFrame) -> dict:
    out = {"likely_by_slot": {}, "value": {}, "likely_all": summarise(tips.query("product == 'likely'"))}
    for slot, g in tips.query("product == 'likely'").groupby("slot"):
        out["likely_by_slot"][slot] = summarise(g)
    value = tips.query("product == 'value'")
    out["value"] = {"ref": summarise(value), "b365_early": summarise(value, "price_b365"),
                    "b365_close": summarise(value, "price_b365c")}
    out["value_sources"] = value["source"].value_counts().to_dict()
    return out


def value_eligibility(tips: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for code, g in tips.query("product == 'value'").groupby("league"):
        s = summarise(g)
        rows.append({"league": code, "value_tips": s["tips"], "clv": s["clv"], "clv_lo": s["clv_lo"],
                     "clv_hi": s["clv_hi"], "roi": s["roi"], "roi_lo": s["roi_lo"], "roi_hi": s["roi_hi"],
                     "eligible": bool(s["tips"] >= VALUE_MIN_TIPS and s["clv_lo"] > 0)})
    return pd.DataFrame(rows)


def slip_table(sl: pd.DataFrame) -> dict:
    out = {}
    for kind, g in sl.groupby("kind"):
        profit = np.where(g["won"], g["price"] - 1, -1.0)
        out[kind] = {"slips": len(g), "reached": float(g["reached"].mean()),
                     "hit_rate": float(g["won"].mean()), "avg_price": float(g["price"].mean()),
                     "mean_p_all": float(g["p_all"].mean()), "units": float(profit.sum()), "roi": float(profit.mean())}
    return out


def charts(out_dir: Path, pred: pd.DataFrame, tips: pd.DataFrame) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    files = []
    y = outcome_1x2(pred.hg, pred.ag)
    _, table = calibration(model_1x2(pred).ravel(), np.eye(3)[y].ravel())
    fig, ax = plt.subplots(figsize=(5, 5))
    ax.plot([0, 1], [0, 1], color="#999", lw=1, ls="--", label="perfect")
    ax.plot([t[0] for t in table], [t[1] for t in table], marker="o", color="#c2410c", label="model (1X2)")
    ax.set_xlabel("predicted probability"), ax.set_ylabel("observed frequency"), ax.legend()
    ax.set_title("Calibration, holdout 2023/24-2024/25")
    fig.tight_layout(), fig.savefig(out_dir / "calibration.png", dpi=120), plt.close(fig)
    files.append("calibration.png")

    fig, ax = plt.subplots(figsize=(7, 4))
    for label, g in (("most-likely (priced)", tips.query("product == 'likely'")),
                     ("value", tips.query("product == 'value'"))):
        g = g[g["price"].notna()].sort_values("date")
        if len(g):
            ax.plot(g["date"], np.cumsum(np.where(g["won"], g["price"] - 1, -1.0)), label=label)
    ax.axhline(0, color="#999", lw=1)
    ax.set_ylabel("units (1 unit per tip)"), ax.legend(), ax.set_title("Cumulative units, holdout")
    fig.tight_layout(), fig.savefig(out_dir / "units.png", dpi=120), plt.close(fig)
    files.append("units.png")
    return files


def run(quick: bool = False, workers: int | None = None, out_root: Path | None = None) -> Path:
    codes = QUICK_LEAGUES if quick else tuple(fd.LEAGUES)
    leagues = {c: fd.load(c) for c in codes}
    leagues = {c: df for c, df in leagues.items() if not df.empty}
    out_dir = (out_root or fd.REPO_ROOT / "reports") / f"backtest-{date.today().isoformat()}{'-quick' if quick else ''}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Backtest on {len(leagues)} leagues -> {out_dir}", flush=True)

    print("Tuning on 2019/20-2022/23", flush=True)
    cfg, tuning = tune(leagues, workers)
    pcfg = ProductConfig(edge=tuning["chosen_edge"])

    print("Holdout 2023/24-2024/25", flush=True)
    hold = _quality(walk_forward_all(leagues, *HOLD, cfg, workers))
    gate = league_gate(hold, "psc", leagues)
    tips = simulate(hold, pcfg, "psc")
    eligibility = value_eligibility(tips)
    eligible = set(eligibility.query("eligible").league) & set(gate.query("gate").league)

    w = tuning["blend"]["best_w"]
    blend_rows = hold.dropna(subset=["ps_h", "ps_d", "ps_a", "psc_h", "psc_d", "psc_a"])
    yb = outcome_1x2(blend_rows.hg, blend_rows.ag)
    blend_result = {"w": w, "ll_model": log_loss(model_1x2(blend_rows), yb),
                    "ll_blend": log_loss(blend_1x2(blend_rows, w, "ps"), yb),
                    "ll_pinnacle_close": log_loss(bench_1x2(blend_rows, "psc"), yb)}

    print("Final holdout 2025/26 (frozen settings)", flush=True)
    final = _quality(walk_forward_all(leagues, *FINAL, cfg, workers))
    final_gate = league_gate(final, "avgc", leagues) if not final.empty else pd.DataFrame()
    final_tips = simulate(final, pcfg, "avgc") if not final.empty else pd.DataFrame()
    final_value = value_eligibility(final_tips) if not final_tips.empty else pd.DataFrame()
    kept = sorted(l for l in eligible if not final_value.empty
                  and (final_value.set_index("league").get("clv", pd.Series(dtype=float)).get(l, -1) > 0))

    results = {
        "settings": {"model": asdict(cfg), "products": asdict(pcfg)},
        "tuning": tuning, "blend_holdout": blend_result,
        "holdout_products": product_tables(tips), "holdout_slips": slip_table(slips(tips)),
        "value_eligible_after_holdout": sorted(eligible), "value_kept_after_final": kept,
        "final_products": product_tables(final_tips) if not final_tips.empty else {},
    }
    gate.to_csv(out_dir / "league_gate_holdout.csv", index=False)
    eligibility.to_csv(out_dir / "value_eligibility_holdout.csv", index=False)
    if not final_gate.empty:
        final_gate.to_csv(out_dir / "league_gate_final.csv", index=False)
        final_value.to_csv(out_dir / "value_final.csv", index=False)
    tips.to_csv(out_dir / "holdout_tips.csv", index=False)
    (out_dir / "results.json").write_text(json.dumps(results, indent=2, default=float), encoding="utf-8")
    images = charts(out_dir, hold, tips)

    from engrebodds_model.backtest.report import write_report
    write_report(out_dir, results, gate, eligibility, final_gate, final_value, images, quick)
    print(f"Report: {out_dir / 'report.md'}", flush=True)
    return out_dir
