"""Markdown report for a backtest run (private use only; written to git-ignored reports/backtest-*)."""

from pathlib import Path

import numpy as np
import pandas as pd


def _pct(x, digits=1) -> str:
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:+.{digits}f}%"


def _rate(x) -> str:
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:.1f}%"


def _num(x, digits=2) -> str:
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{digits}f}"


def _roi(s: dict) -> str:
    if not s or not s.get("priced"):
        return "— (no market price)"
    flag = " **inconclusive**" if s.get("roi_inconclusive") else ""
    return f"{_pct(s['roi'])} [{_pct(s['roi_lo'])}, {_pct(s['roi_hi'])}]{flag}"


def _clv(s: dict) -> str:
    if not s or not s.get("clv_n"):
        return "—"
    return f"{_pct(s['clv'], 2)} [{_pct(s['clv_lo'], 2)}, {_pct(s['clv_hi'], 2)}] (n={s['clv_n']})"


def _summary_row(label: str, s: dict) -> str:
    return (f"| {label} | {s.get('tips', 0)} | {_rate(s.get('hit_rate'))} | {s.get('priced', 0)} | "
            f"{s.get('no_market_price', 0)} | {_num(s.get('avg_odds'))} | {_num(s.get('units'), 1)} | {_roi(s)} | "
            f"{_num(s.get('max_drawdown'), 1)} | {_clv(s)} |")


SUMMARY_HEAD = ("| | Tips | Hit rate | Priced | No market price | Avg odds (priced) | Units | ROI [95% CI] | "
                "Max drawdown | Mean CLV [95% CI] |\n|---|---|---|---|---|---|---|---|---|---|")


def write_report(out_dir: Path, results: dict, gate: pd.DataFrame, eligibility: pd.DataFrame,
                 final_gate: pd.DataFrame, final_value: pd.DataFrame, images: list[str], quick: bool) -> None:
    t, hp = results["tuning"], results["holdout_products"]
    L = ["# Engrebodds backtest" + (" (quick run: 5 leagues)" if quick else ""), "",
         "> **Private research only.** Built from football-data.co.uk, whose data is for private individuals, "
         "not commercial use. Don't publish these results or use them to justify publishing tips. "
         "Re-run on licensed odds before any commercial use (docs/phase-2-plan.md, addendum).", "",
         "Walk-forward: refit weekly on matches strictly before each cutoff. Tuning 2019/20–2022/23; "
         "**holdout 2023/24–2024/25** (evaluated once); **final holdout 2025/26** with settings frozen. "
         "Only matches with data quality ≥ 0.6 count (as live).", ""]

    # Tuning
    m = results["settings"]["model"]
    L += ["## 1. Tuning (2019/20–2022/23)", "",
          f"Chosen: time decay ξ = {m['xi']}/day, ridge penalty {m['penalty']}, half-time share: "
          f"{t['ht_share']['chosen']}, value edge {t['chosen_edge']:.0%}.", "",
          "| ξ | Penalty | Matches | 1X2 log loss |", "|---|---|---|---|"]
    for g in sorted(t["grid"], key=lambda g: g["ll_1x2"]):
        L.append(f"| {g['xi']} | {g['penalty']} | {g['matches']} | {g['ll_1x2']:.4f} |")
    ht = t["ht_share"]
    L += ["", f"Half-time share: per league log loss {ht['per_league_ll']:.4f} vs global "
          f"({ht['global_share']:.3f}) {ht['global_ll']:.4f}.", "",
          "Value edge (tuning period, CLV against Pinnacle closing). The chosen edge has the best lower bound "
          "on mean CLV; if every edge is negative, that is simply the least bad, and no value product is justified:", "",
          "| Edge | Value tips | Mean CLV [95% CI] | ROI |", "|---|---|---|---|"]
    for e, s in t["edge"].items():
        L.append(f"| {float(e):.0%} | {s['tips']} | {_pct(s['clv'], 2)} [{_pct(s['clv_lo'], 2)}, "
                 f"{_pct(s['clv_hi'], 2)}] | {_pct(s['roi'])} |")

    # Holdout gate
    L += ["", "## 2. League gate (holdout 2023/24–2024/25)", "",
          f"Pass = 1X2 log loss ≤ Pinnacle closing (margin removed) + 0.01 **and** calibration error ≤ 3pp. "
          f"{int(gate['gate'].sum()) if len(gate) else 0} of {len(gate)} leagues pass.", "",
          "| League | Matches | Model | Pinnacle close | Base rates | Gap | Calibration error | Gate |",
          "|---|---|---|---|---|---|---|---|"]
    for r in gate.sort_values("ll_gap").itertuples():
        L.append(f"| {r.name} | {r.matches} | {r.ll_model:.4f} | {r.ll_bench:.4f} | {r.ll_base:.4f} | "
                 f"{r.ll_gap:+.4f} | {r.ece * 100:.1f}pp | {'pass' if r.gate else 'fail'} |")
    if images:
        L += ["", "![calibration](calibration.png)"]

    # Products
    L += ["", "## 3. Most-likely tips (holdout)", "",
          "Hit rate counts every tip; units, ROI and average odds count only tips with a market price "
          "(builder, GG/NG and most over/under lines have none in these files).", "", SUMMARY_HEAD,
          _summary_row("**All most-likely**", hp["likely_all"])]
    for slot, s in hp["likely_by_slot"].items():
        L.append(_summary_row(slot, s))
    v = hp["value"]
    src = ", ".join(f"{k} {n}" for k, n in hp.get("value_sources", {}).items()) or "—"
    L += ["", "## 4. Value tips (holdout): the paid product", "",
          f"1X2 and O/U 2.5 only, at most one per match, edge ≥ {t['chosen_edge']:.0%}. "
          f"Reference price taken from: {src} (1xBet isn't in most files; Betway and Bet365 stand in).", "",
          SUMMARY_HEAD, _summary_row("At reference price", v["ref"]),
          _summary_row("At Bet365 early", v["b365_early"]), _summary_row("At Bet365 closing", v["b365_close"]),
          "", "Value eligibility per league (needs ≥ 150 value tips **and** CLV interval lower bound > 0, "
          "plus passing the league gate):", "",
          "| League | Value tips | Mean CLV [95% CI] | ROI [95% CI] | Eligible |", "|---|---|---|---|---|"]
    for r in eligibility.sort_values("clv", ascending=False).itertuples():
        L.append(f"| {r.league} | {r.value_tips} | {_pct(r.clv, 2)} [{_pct(r.clv_lo, 2)}, {_pct(r.clv_hi, 2)}] | "
                 f"{_pct(r.roi)} [{_pct(r.roi_lo)}, {_pct(r.roi_hi)}] | {'yes' if r.eligible else 'no'} |")
    L += ["", f"**Eligible after holdout (and gate):** {', '.join(results['value_eligible_after_holdout']) or 'none'}"]
    if images:
        L += ["", "![units](units.png)"]

    # Slips
    L += ["", "## 5. Slips (holdout)", "",
          "Built daily from priced most-likely tips (result, O/U 2.5) with p ≥ 0.60 (lowest-risk: ≥ 0.80), one leg per match.",
          "", "| Slip | Count | Reached target | Hit rate | Avg price | Model chance all land | Units | ROI |",
          "|---|---|---|---|---|---|---|---|"]
    for kind, s in results["holdout_slips"].items():
        L.append(f"| {kind} | {s['slips']} | {_rate(s['reached'])} | {_rate(s['hit_rate'])} | {s['avg_price']:.2f} | "
                 f"{_rate(s['mean_p_all'])} | {s['units']:.1f} | {_pct(s['roi'])} |")

    # Blend
    b = results["blend_holdout"]
    L += ["", "## 6. Blend variant (holdout)", "",
          f"p = w·model + (1−w)·Pinnacle early (margin removed), w = {b['w']} chosen in tuning. "
          f"1X2 log loss: model {b['ll_model']:.4f}, blend {b['ll_blend']:.4f}, "
          f"Pinnacle closing {b['ll_pinnacle_close']:.4f}."]

    # Final holdout
    L += ["", "## 7. Final holdout 2025/26 (settings frozen)", "",
          "Benchmark: market-average closing price with the margin removed (Pinnacle unreliable since 23 Jul 2025). "
          "It can only remove leagues from the value product.", ""]
    if final_gate is not None and len(final_gate):
        L += ["| League | Matches | Model | Market avg close | Gap | Calibration error |", "|---|---|---|---|---|---|"]
        for r in final_gate.sort_values("ll_gap").itertuples():
            L.append(f"| {r.name} | {r.matches} | {r.ll_model:.4f} | {r.ll_bench:.4f} | {r.ll_gap:+.4f} | "
                     f"{r.ece * 100:.1f}pp |")
    fp = results.get("final_products") or {}
    if fp:
        L += ["", SUMMARY_HEAD, _summary_row("Most-likely", fp["likely_all"]),
              _summary_row("Value (reference price)", fp["value"]["ref"])]
    L += ["", f"**Value leagues kept after the final holdout:** {', '.join(results['value_kept_after_final']) or 'none'}"]
    (out_dir / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
