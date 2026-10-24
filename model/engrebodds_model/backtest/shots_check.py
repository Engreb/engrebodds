"""Do football-data's shots on target (HST/AST) agree with API-Football's "Shots on Goal"?

The shots experiment is tuned on football-data but would run live on API-Football statistics, so the two
must measure the same thing. Compares a spread sample of Premier League 2024/25 matches (~20 API requests).
Matches are paired by date and score, with team-name similarity to break ties.
"""

import json
from difflib import SequenceMatcher

import numpy as np

from engrebodds_ingest.api import ApiFootball
from engrebodds_ingest.config import load_settings
from engrebodds_model.backtest import football_data as fd

AUDIT_FIXTURES = fd.REPO_ROOT / "ingest" / "data" / "audit" / "2026-10-21" / "season_39_2024.json"
OUT = fd.REPO_ROOT / "ingest" / "data" / "shots_check.json"


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def run(sample: int = 20) -> dict:
    fdx = fd.load("E0", ["2425"])
    fixtures = [f for f in json.loads(AUDIT_FIXTURES.read_text(encoding="utf-8"))["response"]
                if f["fixture"]["status"]["short"] == "FT"]
    fixtures.sort(key=lambda f: f["fixture"]["date"])
    picks = fixtures[:: max(1, len(fixtures) // sample)][:sample]

    api = ApiFootball(load_settings().api_key)
    pairs = []
    for f in picks:
        day = f["fixture"]["date"][:10]
        home, away = f["teams"]["home"]["name"], f["teams"]["away"]["name"]
        cands = fdx[(fdx.date.dt.strftime("%Y-%m-%d") == day) & (fdx.hg == f["goals"]["home"])
                    & (fdx.ag == f["goals"]["away"])]
        if cands.empty:
            continue
        row = max(cands.itertuples(), key=lambda r: _similar(r.home, home) + _similar(r.away, away))
        stats = api.get("/fixtures/statistics", fixture=f["fixture"]["id"])["response"]
        sog = {}
        for team in stats:
            value = next((s["value"] for s in team["statistics"] if s["type"] == "Shots on Goal"), None)
            sog[team["team"]["id"]] = int(value or 0)
        api_home, api_away = sog.get(f["teams"]["home"]["id"]), sog.get(f["teams"]["away"]["id"])
        if api_home is None or api_away is None:
            continue
        pairs.append({"date": day, "match": f"{home} v {away}", "fd": [int(row.hst), int(row.ast)],
                      "api": [api_home, api_away]})
    api.close()

    fd_vals = np.array([v for p in pairs for v in p["fd"]])
    api_vals = np.array([v for p in pairs for v in p["api"]])
    diff = api_vals - fd_vals
    result = {
        "matches": len(pairs), "requests": api.requests_made,
        "exact_share": float(np.mean(diff == 0)) if len(diff) else None,
        "mean_abs_diff": float(np.mean(np.abs(diff))) if len(diff) else None,
        "mean_diff_api_minus_fd": float(np.mean(diff)) if len(diff) else None,
        "correlation": float(np.corrcoef(fd_vals, api_vals)[0, 1]) if len(diff) > 2 else None,
        "pairs": pairs,
    }
    # Agreement rule: highly correlated, small average difference, no systematic offset.
    result["agree"] = bool(result["correlation"] and result["correlation"] >= 0.9
                           and result["mean_abs_diff"] <= 1.0 and abs(result["mean_diff_api_minus_fd"]) <= 0.5)
    OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
