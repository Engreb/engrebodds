"""Coverage audit: what API-Football actually gives us, before we build on it.

Uses roughly 15-20 requests, so it fits the free plan (100/day). Writes a Markdown
report to reports/ and the raw responses to ingest/data/audit/ (git-ignored).
"""

import json
import re
from collections import Counter
from datetime import date, timedelta

from engrebodds_ingest.api import ApiError, ApiFootball
from engrebodds_ingest.config import REPO_ROOT, Settings

# Leagues we always cover, whatever the coverage flags say.
PINNED_NAME = re.compile(r"^CAF |Africa Cup of Nations|Qualification Africa")
PINNED_COUNTRY = "Nigeria"
NPFL_NAME = "NPFL"

# Benchmark leagues for comparing data quality: Premier League, LaLiga, Serie A, Bundesliga, Ligue 1, Eredivisie.
BENCHMARK_IDS = (39, 140, 135, 78, 61, 88)

# Markets the site needs, by API-Football bet name.
KEY_MARKETS = (
    "Match Winner",
    "Double Chance",
    "Goals Over/Under",
    "Both Teams Score",
    "Exact Score",
    "Home Team Score a Goal",
    "Clean Sheet - Home",
    "Corners Over Under",
    "Cards Over/Under",
)

FINISHED = {"FT", "AET", "PEN"}
VOID = {"CANC", "ABD", "AWD", "WO"}


# ---------- pure analysis (tested) ----------

def current_season(entry: dict) -> dict | None:
    return next((s for s in entry["seasons"] if s.get("current")), None)


def is_pinned(entry: dict) -> bool:
    return entry["country"]["name"] == PINNED_COUNTRY or bool(PINNED_NAME.search(entry["league"]["name"] or ""))


def has_odds_and_stats(season: dict) -> bool:
    cov = season.get("coverage") or {}
    return bool(cov.get("odds")) and bool((cov.get("fixtures") or {}).get("statistics_fixtures"))


def summarise_leagues(entries: list[dict]) -> dict:
    """Count leagues by coverage and build the proposed allowlist."""
    with_current = [e for e in entries if current_season(e)]
    odds = [e for e in with_current if (current_season(e)["coverage"] or {}).get("odds")]
    odds_stats = [e for e in with_current if has_odds_and_stats(current_season(e))]
    pinned = [e for e in entries if is_pinned(e)]
    allow_ids = {e["league"]["id"] for e in odds_stats} | {e["league"]["id"] for e in pinned if current_season(e)}
    return {
        "total": len(entries),
        "with_current": len(with_current),
        "with_odds": len(odds),
        "with_odds_and_stats": len(odds_stats),
        "pinned": pinned,
        "allowlist_ids": allow_ids,
        "allowlist_by_type": Counter(e["league"]["type"] for e in entries if e["league"]["id"] in allow_ids),
    }


def season_quality(fixtures: list[dict]) -> dict:
    """Completeness of a season's results: statuses, missing 90-minute or half-time scores."""
    status = Counter(f["fixture"]["status"]["short"] for f in fixtures)
    finished = [f for f in fixtures if f["fixture"]["status"]["short"] in FINISHED]
    no_ft = sum(1 for f in finished if f["score"]["fulltime"]["home"] is None)
    no_ht = sum(1 for f in finished if f["score"]["halftime"]["home"] is None)
    return {
        "fixtures": len(fixtures),
        "finished": len(finished),
        "void": sum(status[s] for s in VOID),
        "postponed": status["PST"],
        "finished_missing_ft": no_ft,
        "finished_missing_ht": no_ht,
        "status": dict(status.most_common()),
    }


def odds_summary(odds_response: list[dict]) -> dict:
    """Bookmakers and key markets present for one fixture."""
    books = odds_response[0]["bookmakers"] if odds_response else []
    bets = {b["name"] for bk in books for b in bk["bets"] if b.get("name")}
    return {
        "bookmakers": sorted(bk["name"] for bk in books if bk.get("name")),
        "markets_total": len(bets),
        "key_markets": {m: (m in bets) for m in KEY_MARKETS},
    }


def stats_summary(stats_response: list[dict]) -> list[str]:
    """Statistic types with a value for both teams."""
    if len(stats_response) < 2:
        return []
    have = [{s["type"] for s in t["statistics"] if s["value"] is not None} for t in stats_response]
    return sorted(have[0] & have[1])


# ---------- the job ----------

class Audit:
    def __init__(self, api: ApiFootball):
        self.api = api
        self.raw: dict[str, object] = {}
        self.errors: list[str] = []

    def fetch(self, key: str, endpoint: str, **params) -> list | dict | None:
        try:
            body = self.api.get(endpoint, **params)
        except ApiError as e:
            self.errors.append(f"`{endpoint}` {params}: {e.errors}")
            return None
        self.raw[key] = body
        return body["response"]

    def latest_past_season(self, league_id: int, current_year: int) -> tuple[int, list] | None:
        """Most recent completed season the plan lets us read."""
        for year in range(current_year - 1, current_year - 5, -1):
            fx = self.fetch(f"season_{league_id}_{year}", "/fixtures", league=league_id, season=year)
            if fx:
                return year, fx
        return None


def first_finished(fixtures: list[dict]) -> dict | None:
    return next((f for f in reversed(fixtures) if f["fixture"]["status"]["short"] in FINISHED), None)


def run(settings: Settings) -> None:
    api = ApiFootball(settings.api_key)
    a = Audit(api)
    today = date.today()

    status = a.fetch("status", "/status") or {}
    leagues = a.fetch("leagues", "/leagues") or []
    bookmakers = a.fetch("bookmakers", "/odds/bookmakers") or []
    summary = summarise_leagues(leagues)

    # Fixtures by date (all leagues in one call per day). The free plan may block the current season.
    by_day = {}
    for off in range(-1, 3):
        d = (today + timedelta(days=off)).isoformat()
        fx = a.fetch(f"fixtures_{d}", "/fixtures", date=d)
        if fx is not None:
            by_day[d] = fx

    # Past-season completeness: NPFL against the Premier League.
    npfl = next((e for e in summary["pinned"] if e["league"]["name"] == NPFL_NAME and e["country"]["name"] == PINNED_COUNTRY), None)
    quality, stats = {}, {}
    for label, entry in (("NPFL", npfl), ("Premier League", next((e for e in leagues if e["league"]["id"] == 39), None))):
        if not entry or not current_season(entry):
            continue
        found = a.latest_past_season(entry["league"]["id"], current_season(entry)["year"])
        if not found:
            continue
        year, fx = found
        quality[f"{label} {year}"] = season_quality(fx)
        done = first_finished(fx)
        if done:
            st = a.fetch(f"stats_{done['fixture']['id']}", "/fixtures/statistics", fixture=done["fixture"]["id"])
            stats[f"{label}: {done['teams']['home']['name']} v {done['teams']['away']['name']}"] = stats_summary(st or [])

    # Odds samples on upcoming fixtures: one benchmark league, NPFL, and a CAF competition.
    upcoming = [f for d, fx in sorted(by_day.items()) if d >= today.isoformat() for f in fx
                if f["fixture"]["status"]["short"] == "NS"]
    picks = {
        "Benchmark league": next((f for f in upcoming if f["league"]["id"] in BENCHMARK_IDS), None),
        "NPFL": next((f for f in upcoming if npfl and f["league"]["id"] == npfl["league"]["id"]), None),
        "CAF": next((f for f in upcoming if f["league"]["name"].startswith("CAF ")), None),
    }
    odds = {}
    for label, f in picks.items():
        if not f:
            odds[label] = None
            continue
        resp = a.fetch(f"odds_{f['fixture']['id']}", "/odds", fixture=f["fixture"]["id"])
        odds[f"{label}: {f['teams']['home']['name']} v {f['teams']['away']['name']} ({f['league']['name']})"] = (
            odds_summary(resp or [])
        )

    # Save raw responses first, so a report bug never wastes the requests already spent.
    raw_dir = REPO_ROOT / "ingest" / "data" / "audit" / today.isoformat()
    raw_dir.mkdir(parents=True, exist_ok=True)
    for key, body in a.raw.items():
        (raw_dir / f"{key}.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    api.close()

    report = render_report(today, status, summary, bookmakers, by_day, quality, stats, odds, a.errors, api)
    out = REPO_ROOT / "reports" / f"coverage-audit-{today.isoformat()}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(report, encoding="utf-8")

    print(f"Report: {out}")
    print(f"Requests used: {api.requests_made}; quota remaining today: {api.quota_remaining}")


# ---------- report ----------

def yn(v) -> str:
    return "yes" if v else "no"


def render_report(today, status, summary, bookmakers, by_day, quality, stats, odds, errors, api) -> str:
    sub = (status or {}).get("subscription", {}) if isinstance(status, dict) else {}
    req = (status or {}).get("requests", {}) if isinstance(status, dict) else {}
    allow = summary["allowlist_ids"]
    L = [f"# API-Football coverage audit, {today.isoformat()}", ""]
    L += [f"Plan: **{sub.get('plan', '?')}** · daily limit {req.get('limit_day', '?')} · "
          f"requests used by this audit: {api.requests_made}", ""]

    L += ["## Leagues", "",
          "| | Count |", "|---|---|",
          f"| Leagues listed | {summary['total']} |",
          f"| With a current season | {summary['with_current']} |",
          f"| Current season has odds | {summary['with_odds']} |",
          f"| Current season has odds **and** match statistics | {summary['with_odds_and_stats']} |",
          f"| **Proposed allowlist** (odds + statistics, plus pinned) | **{len(allow)}** |", ""]
    L += ["By type: " + ", ".join(f"{k} {v}" for k, v in summary["allowlist_by_type"].items()), ""]

    L += ["## Pinned leagues (Nigeria and CAF)", "",
          "| League | Country | Current season | Odds | Match stats | Standings | Events |",
          "|---|---|---|---|---|---|---|"]
    for e in sorted(summary["pinned"], key=lambda e: (e["country"]["name"], e["league"]["name"])):
        s = current_season(e)
        cov = (s or {}).get("coverage") or {}
        fxc = cov.get("fixtures") or {}
        L.append(f"| {e['league']['name']} ({e['league']['id']}) | {e['country']['name']} | "
                 f"{s['year'] if s else 'none'} | {yn(cov.get('odds'))} | {yn(fxc.get('statistics_fixtures'))} | "
                 f"{yn(cov.get('standings'))} | {yn(fxc.get('events'))} |")
    L.append("")

    L += ["## Fixtures per day", ""]
    if by_day:
        L += ["| Date | All fixtures | On proposed allowlist |", "|---|---|---|"]
        for d, fx in sorted(by_day.items()):
            L.append(f"| {d} | {len(fx)} | {sum(1 for f in fx if f['league']['id'] in allow)} |")
    else:
        L.append("No date queries succeeded (see errors). The free plan probably blocks the current season.")
    L.append("")

    L += ["## Past-season completeness", ""]
    if quality:
        L += ["| Season | Fixtures | Finished | Postponed | Void | Finished, no FT score | Finished, no HT score |",
              "|---|---|---|---|---|---|---|"]
        for k, q in quality.items():
            L.append(f"| {k} | {q['fixtures']} | {q['finished']} | {q['postponed']} | {q['void']} | "
                     f"{q['finished_missing_ft']} | {q['finished_missing_ht']} |")
    else:
        L.append("No past season was readable.")
    L.append("")

    L += ["## Match statistics sample", ""]
    for k, types in stats.items():
        L.append(f"- {k}: {', '.join(types) if types else '**no statistics returned**'}")
    if not stats:
        L.append("No sample fetched.")
    L.append("")

    L += ["## Odds samples (upcoming fixtures)", ""]
    for k, o in odds.items():
        if o is None:
            L.append(f"- {k}: no upcoming fixture found in the date window")
            continue
        L.append(f"- **{k}**: {len(o['bookmakers'])} bookmakers, {o['markets_total']} markets")
        if o["bookmakers"]:
            L.append(f"  - Bookmakers: {', '.join(o['bookmakers'])}")
            L.append("  - Key markets: " + ", ".join(f"{m} {yn(v)}" for m, v in o["key_markets"].items()))
    L.append("")

    names = sorted(b["name"] for b in bookmakers if b.get("name"))
    L += ["## Bookmakers API-Football carries", "", f"{len(names)} in total: {', '.join(names)}", ""]
    nigerian = ["SportyBet", "Bet9ja", "BetKing", "1xBet", "Betway", "MSport", "NairaBet"]
    L.append("Nigerian-market bookmakers present: " +
             ", ".join(f"{n} {yn(any(n.lower() in x.lower() for x in names))}" for n in nigerian))
    L.append("")

    if errors:
        L += ["## Errors returned", ""] + [f"- {e}" for e in errors] + [""]
    return "\n".join(L)
