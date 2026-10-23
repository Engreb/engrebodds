"""Turn API-Football responses into rows that match our tables.

Pure functions only (no I/O), so they are tested against recorded responses in tests/fixtures/.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

FINAL = {"FT", "AET", "PEN"}

# ---------- odds ----------


@dataclass(frozen=True)
class OddsRow:
    api_fixture_id: int
    api_bookmaker_id: int
    market_code: str
    selection: str
    line: Decimal | None
    price: Decimal


Parsed = tuple[str, Decimal | None] | None   # (selection, line), or None to skip the value

_RESULT = {"Home": "1", "Draw": "X", "Away": "2"}
_DC = {"Home/Draw": "1X", "Draw/Away": "X2", "Home/Away": "12"}
_YES_NO = {"Yes": "Y", "No": "N"}
_OU = re.compile(r"^(Over|Under) (\d+(?:\.\d+)?)$")
_SCORE = re.compile(r"^(\d{1,2}):(\d{1,2})$")


def _lookup(table: dict[str, str]) -> Callable[[str], Parsed]:
    def parse(value: str) -> Parsed:
        sel = table.get(value)
        return (sel, None) if sel else None
    return parse


def _over_under(team: str = "") -> Callable[[str], Parsed]:
    """'Over 2.5' -> ('O', 2.5); with team='H' -> ('HO', 2.5). Quarter lines (2.25, 2.75) are skipped."""
    def parse(value: str) -> Parsed:
        m = _OU.match(value)
        if not m:
            return None
        line = Decimal(m.group(2))
        if (line * 2) % 1:
            return None
        return team + m.group(1)[0], line
    return parse


def _clean_sheet(team: str) -> Callable[[str], Parsed]:
    # Our catalogue only has the "Yes" side ("H" = home keeps a clean sheet).
    return lambda value: (team, None) if value == "Yes" else None


def _score(value: str) -> Parsed:
    m = _SCORE.match(value)
    return (f"{int(m.group(1))}-{int(m.group(2))}", None) if m else None


# API-Football bet id -> (our market code, value parser). Bet ids are the same across bookmakers.
BETS: dict[int, tuple[str, Callable[[str], Parsed]]] = {
    1:  ("1X2",         _lookup(_RESULT)),       # Match Winner
    12: ("DC",          _lookup(_DC)),           # Double Chance
    5:  ("OU",          _over_under()),          # Goals Over/Under
    16: ("TEAM_OU",     _over_under("H")),       # Total - Home
    17: ("TEAM_OU",     _over_under("A")),       # Total - Away
    8:  ("BTTS",        _lookup(_YES_NO)),       # Both Teams Score
    27: ("CLEAN_SHEET", _clean_sheet("H")),      # Clean Sheet - Home
    28: ("CLEAN_SHEET", _clean_sheet("A")),      # Clean Sheet - Away
    10: ("CS",          _score),                 # Exact Score
    13: ("HT_1X2",      _lookup(_RESULT)),       # First Half Winner
    6:  ("HT_OU",       _over_under()),          # Goals Over/Under First Half
    31: ("HT_CS",       _score),                 # Correct Score - First Half
    45: ("CORNERS_OU",  _over_under()),          # Corners Over Under
    80: ("CARDS_OU",    _over_under()),          # Cards Over/Under
}
# No shots or fouls markets appear in API-Football's odds, so tips on those are priced at fair odds.


def _price(odd) -> Decimal | None:
    try:
        price = Decimal(str(odd))
    except (InvalidOperation, ValueError):
        return None
    return price if price > 1 else None


# Lean odds policy (agreed 22 Oct 2026): the displayed bookmaker gets every market we know;
# the other stored bookmakers only these.
MAIN_MARKETS = frozenset({"1X2", "DC", "OU", "BTTS"})

# api_bookmaker_id -> markets to keep (None = all known markets).
OddsPolicy = dict[int, frozenset[str] | None]


def odds_policy(display_api_id: int, other_api_ids: set[int]) -> OddsPolicy:
    return {display_api_id: None} | {i: MAIN_MARKETS for i in other_api_ids if i != display_api_id}


def odds_rows(item: dict, policy: OddsPolicy) -> list[OddsRow]:
    """One element of an /odds response -> rows for the bookmakers and markets the policy keeps."""
    fixture_id = item["fixture"]["id"]
    rows: dict[tuple, OddsRow] = {}
    for bk in item["bookmakers"]:
        if bk["id"] not in policy:
            continue
        allowed = policy[bk["id"]]
        for bet in bk["bets"]:
            mapping = BETS.get(bet["id"])
            if not mapping or (allowed is not None and mapping[0] not in allowed):
                continue
            market, parse = mapping
            for v in bet["values"]:
                parsed = parse(str(v["value"]))
                price = _price(v.get("odd"))
                if not parsed or price is None:
                    continue
                selection, line = parsed
                key = (bk["id"], market, selection, line)
                rows.setdefault(key, OddsRow(fixture_id, bk["id"], market, selection, line, price))
    return list(rows.values())


# ---------- fixtures ----------


@dataclass(frozen=True)
class TeamRef:
    api_team_id: int
    name: str
    logo_url: str | None


@dataclass(frozen=True)
class FixtureRow:
    api_fixture_id: int
    api_league_id: int
    season: int
    round: str | None
    kickoff_at: datetime
    home: TeamRef
    away: TeamRef
    status: str
    elapsed: int | None
    home_ht: int | None
    away_ht: int | None
    home_90: int | None   # API "fulltime" is the 90-minute score
    away_90: int | None
    home_et: int | None   # goals scored in extra time only
    away_et: int | None
    home_pen: int | None
    away_pen: int | None


def _team(t: dict) -> TeamRef:
    return TeamRef(t["id"], t["name"], t.get("logo"))


def fixture_row(item: dict) -> FixtureRow:
    f, s = item["fixture"], item["score"]
    return FixtureRow(
        api_fixture_id=f["id"],
        api_league_id=item["league"]["id"],
        season=item["league"]["season"],
        round=item["league"].get("round"),
        kickoff_at=datetime.fromisoformat(f["date"]),
        home=_team(item["teams"]["home"]),
        away=_team(item["teams"]["away"]),
        status=f["status"]["short"],
        elapsed=f["status"].get("elapsed"),
        home_ht=s["halftime"]["home"], away_ht=s["halftime"]["away"],
        home_90=s["fulltime"]["home"], away_90=s["fulltime"]["away"],
        home_et=s["extratime"]["home"], away_et=s["extratime"]["away"],
        home_pen=s["penalty"]["home"], away_pen=s["penalty"]["away"],
    )


def fixture_issues(row: FixtureRow) -> list[str]:
    """Scores that don't add up. These matches need checking before their tips are trusted.

    No "level score" checks: in the second leg of a two-legged tie, extra time or penalties
    follow a level aggregate, so this match's own score can be anything (e.g. 0-1 then penalties).
    """
    if row.status in FINAL and (row.home_90 is None or row.away_90 is None):
        return ["finished without a 90-minute score"]
    if row.status == "PEN" and (row.home_pen is None or row.away_pen is None):
        return ["PEN without a penalty score"]
    return []


# ---------- match statistics ----------

# API statistic type -> fixture_team_stats column (counts).
STAT_COLUMNS = {
    "Corner Kicks": "corners",
    "Yellow Cards": "yellow_cards",
    "Red Cards": "red_cards",
    "Total Shots": "shots_total",
    "Shots on Goal": "shots_on_target",
    "Fouls": "fouls",
}


@dataclass(frozen=True)
class TeamStatsRow:
    api_team_id: int
    corners: int | None
    yellow_cards: int | None
    red_cards: int | None
    shots_total: int | None
    shots_on_target: int | None
    fouls: int | None
    possession: Decimal | None
    xg: Decimal | None
    raw: list


def _decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value).rstrip("%"))
    except InvalidOperation:
        return None


def team_stats_rows(response: list[dict]) -> list[TeamStatsRow]:
    """/fixtures/statistics response -> one row per team.

    A statistic listed with a null value means zero (API-Football reports 0 as null);
    a statistic missing from the list means it isn't tracked for this match, and stays None.
    """
    rows = []
    for team in response:
        values = {s["type"]: s["value"] for s in team["statistics"]}
        counts = {col: (int(values[t] or 0) if t in values else None) for t, col in STAT_COLUMNS.items()}
        rows.append(TeamStatsRow(
            api_team_id=team["team"]["id"],
            possession=_decimal(values.get("Ball Possession")),
            xg=_decimal(values.get("expected_goals")),
            raw=team["statistics"],
            **counts,
        ))
    return rows
