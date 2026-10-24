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


# ---------- model improvement report ----------

def write_improvement_report(out_dir: Path, r: dict, gates: dict[str, pd.DataFrame], eligibility: pd.DataFrame) -> None:
    L = ["# Model improvement backtest", "",
         "> **Private research only.** Built from football-data.co.uk (private use only, no commercial use). "
         "Nothing here may be used commercially or to justify publishing tips; production parameters are tuned "
         "separately on API-Football data. See docs/model-improvement-plan.md.", "",
         "Every experiment was chosen on earlier seasons and judged on the next (rolling validation over "
         "2020/21, 2021/22, 2022/23). Accepted only if better on average **and** in at least 2 of the 3 seasons. "
         "The 2023/24–2024/25 holdout and 2025/26 had been looked at once before (first backtest); 2026/27 to date "
         "has never been used and is only a comparison (its benchmark is the market-average closing price, not Pinnacle).",
         "", "## 1. Experiment ladder", "",
         "| Experiment | 2020/21 | 2021/22 | 2022/23 | Mean change | Better in | Verdict | Matches compared |",
         "|---|---|---|---|---|---|---|---|"]
    for step in r["ladder"]:
        if "skipped" in step:
            L.append(f"| {step['name']} | — | — | — | — | — | skipped: {step['skipped']} | — |")
            continue
        d = step["diffs"]
        L.append(f"| {step['name']} | {d[0]:+.4f} | {d[1]:+.4f} | {d[2]:+.4f} | {step['mean']:+.4f} | "
                 f"{step['wins']}/3 | {'**accepted**' if step['accepted'] else 'rejected'} | "
                 f"{step.get('matches_compared', '—')} |")
    L += ["", "Changes are in log loss (negative = better): 1X2 for experiments 1, 2, 4, 5 and 3b; the average of "
          "the goals markets (O/U 0.5–4.5, GG/NG, team totals) for 3a.", ""]
    cov = [s for s in r["ladder"] if "coverage_before" in s]
    if cov:
        L.append("Coverage (validation-season matches with data quality ≥ 0.6): " + "; ".join(
            f"{s['name'].split('.')[0]}: {s['coverage_before']} → {s['coverage_after']}" for s in cov))
    c = r["final_config"]
    L += ["", f"**Final model:** ξ = {c['xi']}, penalty {c['penalty']}, linked divisions {c['link_divisions']}, "
          f"new-team prior {c['new_team_prior']}, season reset {c['season_reset']}, shots weight {c['shots_weight']}; "
          f"goals calibration {r['calibration']['goals']}, draw shift {r['calibration']['draw']}; value edge {r['edge']:.0%}."]

    s = r["holdout_same_matches"]
    L += ["", "## 2. Holdout 2023/24–2024/25: first model vs improved", "",
          f"On the same {s['matches']} matches: 1X2 log loss **first model {s['first_model']:.4f} → improved "
          f"{s['improved']:.4f}**; Pinnacle closing {s['pinnacle_close']:.4f}. Gap to the market: "
          f"{s['first_model'] - s['pinnacle_close']:+.4f} → {s['improved'] - s['pinnacle_close']:+.4f}.", "",
          f"Coverage (matches with data quality ≥ 0.6): {s['coverage_first']} → {s['coverage_improved']}; "
          f"July–September: {s['early_season_first']} → {s['early_season_improved']}.", ""]

    L += ["## 3. League gate (holdout)", "",
          "Pass = 1X2 log loss ≤ Pinnacle closing + 0.01 and calibration error ≤ 3pp. Experiment 0 is the early "
          "market price itself (what the most-likely product would publish).", "",
          "| League | First model gap | Improved gap | Improved gate | Experiment 0 gap | Experiment 0 gate |",
          "|---|---|---|---|---|---|"]
    first = gates["First backtest model"].set_index("league")
    imp = gates["Improved model"].set_index("league")
    exp0 = gates["Experiment 0 (early market, 1X2)"].set_index("league")
    for code in imp.sort_values("ll_gap").index:
        L.append(f"| {imp.loc[code, 'name']} | {first['ll_gap'].get(code, np.nan):+.4f} | {imp.loc[code, 'll_gap']:+.4f} | "
                 f"{'pass' if imp.loc[code, 'gate'] else 'fail'} | {exp0['ll_gap'].get(code, np.nan):+.4f} | "
                 f"{'pass' if exp0['gate'].get(code, False) else 'fail'} |")
    L += ["", "Leagues passing: " + "; ".join(f"{n} {int(g['gate'].sum())}/{len(g)}" for n, g in gates.items())]

    L += ["", "## 4. Most-likely product (holdout)", "", SUMMARY_HEAD]
    for name, p in r["products"].items():
        L.append(_summary_row(f"**{name}**", p["likely_all"]))
        for slot, sm in p["likely_by_slot"].items():
            L.append(_summary_row(f"&nbsp;&nbsp;{slot}", sm))

    L += ["", "### Tip calibration (does each slot land as often as it says?)", ""]
    for name, slots in r["tip_calibration"].items():
        L += [f"**{name}**", "", "| Slot | Band | Tips | Predicted | Landed | Gap |", "|---|---|---|---|---|---|"]
        for slot, rows in slots.items():
            for b in rows:
                L.append(f"| {slot} | {b['band']} | {b['n']} | {_rate(b['p'])} | {_rate(b['hit'])} | "
                         f"{(b['hit'] - b['p']) * 100:+.1f}pp |")
        L.append("")

    v = r["products"]["Improved model"]["value"]
    L += ["## 5. Value product (improved model, holdout)", "", SUMMARY_HEAD,
          _summary_row("At reference price", v["ref"]), _summary_row("At Bet365 early", v["b365_early"]),
          _summary_row("At Bet365 closing", v["b365_close"]), "",
          "| League | Value tips | Mean CLV [95% CI] | ROI [95% CI] | Eligible |", "|---|---|---|---|---|"]
    for row in eligibility.sort_values("clv", ascending=False).itertuples():
        L.append(f"| {row.league} | {row.value_tips} | {_pct(row.clv, 2)} [{_pct(row.clv_lo, 2)}, {_pct(row.clv_hi, 2)}] | "
                 f"{_pct(row.roi)} [{_pct(row.roi_lo)}, {_pct(row.roi_hi)}] | {'yes' if row.eligible else 'no'} |")
    L += ["", f"**Value-eligible leagues (and passing the gate):** {', '.join(r['value_eligible']) or 'none'}"]

    L += ["", "## 6. Slips (holdout)", "", "| Version | Slip | Count | Reached target | Hit rate | Model chance all land | ROI |",
          "|---|---|---|---|---|---|---|"]
    for label, table in (("Improved model", r["slips_improved"]), ("Experiment 0", r["slips_market"])):
        for kind, sl in table.items():
            L.append(f"| {label} | {kind} | {sl['slips']} | {_rate(sl['reached'])} | {_rate(sl['hit_rate'])} | "
                     f"{_rate(sl['mean_p_all'])} | {_pct(sl['roi'])} |")

    L += ["", "## 7. Later seasons (same matches for both models)", "",
          "| Season | Matches | First model | Improved | Market-average close | Calibration error first → improved |",
          "|---|---|---|---|---|---|"]
    for label, x in r["later"].items():
        L.append(f"| {label} | {x['matches']} | {x['first_model']:.4f} | {x['improved']:.4f} | {x['market_avg_close']:.4f} | "
                 f"{x['calibration_first'] * 100:.1f}pp → {x['calibration_improved'] * 100:.1f}pp |")
    kept = r["later"].get("2025/26", {}).get("value_kept", [])
    L += ["", f"Value leagues kept after 2025/26: {', '.join(kept) or 'none'}. 2026/27 is a comparison only."]
    (out_dir / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
