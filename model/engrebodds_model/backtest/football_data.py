"""Download and load football-data.co.uk league CSVs (private use only, see package docstring).

Files are cached under ingest/data/football-data/<season>/<code>.csv (git-ignored) and loaded into one
DataFrame per league with standard column names. Odds columns missing from a season come back as NaN.
"""

import time
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "ingest" / "data" / "football-data"
URL = "https://football-data.co.uk/mmz4281/{season}/{code}.csv"
USER_AGENT = "engrebodds-research/0.1 (private, non-commercial use)"

# The 22 main European divisions football-data covers.
LEAGUES = {
    "E0": "England Premier League", "E1": "England Championship", "E2": "England League One",
    "E3": "England League Two", "EC": "England National League",
    "SC0": "Scotland Premiership", "SC1": "Scotland Championship", "SC2": "Scotland League One",
    "SC3": "Scotland League Two", "D1": "Germany Bundesliga", "D2": "Germany 2. Bundesliga",
    "I1": "Italy Serie A", "I2": "Italy Serie B", "SP1": "Spain LaLiga", "SP2": "Spain Segunda",
    "F1": "France Ligue 1", "F2": "France Ligue 2", "N1": "Netherlands Eredivisie",
    "B1": "Belgium Pro League", "P1": "Portugal Primeira Liga", "T1": "Turkey Super Lig", "G1": "Greece Super League",
}
# Season codes "1617" .. "2526". 2016/17-2018/19 are training history only.
SEASONS = [f"{y % 100:02d}{(y + 1) % 100:02d}" for y in range(2016, 2026)]
# The current season (2026/27 to date): used only to compare model versions, never for gate decisions.
CURRENT = "2627"

# Standard name -> football-data column(s), first present wins.
COLUMNS = {
    "hthg": ["HTHG"], "htag": ["HTAG"], "hst": ["HST"], "ast": ["AST"],
    "b365_h": ["B365H"], "b365_d": ["B365D"], "b365_a": ["B365A"],
    "b365c_h": ["B365CH"], "b365c_d": ["B365CD"], "b365c_a": ["B365CA"],
    "b365_o25": ["B365>2.5"], "b365_u25": ["B365<2.5"], "b365c_o25": ["B365C>2.5"], "b365c_u25": ["B365C<2.5"],
    "ps_h": ["PSH", "PH"], "ps_d": ["PSD", "PD"], "ps_a": ["PSA", "PA"],
    "psc_h": ["PSCH"], "psc_d": ["PSCD"], "psc_a": ["PSCA"],
    "ps_o25": ["P>2.5"], "ps_u25": ["P<2.5"], "psc_o25": ["PC>2.5"], "psc_u25": ["PC<2.5"],
    "avg_h": ["AvgH", "BbAvH"], "avg_d": ["AvgD", "BbAvD"], "avg_a": ["AvgA", "BbAvA"],
    "avg_o25": ["Avg>2.5", "BbAv>2.5"], "avg_u25": ["Avg<2.5", "BbAv<2.5"],
    "avgc_h": ["AvgCH"], "avgc_d": ["AvgCD"], "avgc_a": ["AvgCA"],
    "avgc_o25": ["AvgC>2.5"], "avgc_u25": ["AvgC<2.5"],
    "xb_h": ["1XBH"], "xb_d": ["1XBD"], "xb_a": ["1XBA"],
    "xbc_h": ["1XBCH"], "xbc_d": ["1XBCD"], "xbc_a": ["1XBCA"],
    "bw_h": ["BWH"], "bw_d": ["BWD"], "bw_a": ["BWA"],
    "bwc_h": ["BWCH"], "bwc_d": ["BWCD"], "bwc_a": ["BWCA"],
}


def path(code: str, season: str) -> Path:
    return DATA_DIR / season / f"{code}.csv"


def download(codes=LEAGUES, seasons=SEASONS, refresh: bool = False, pause: float = 1.0) -> list[str]:
    """Fetch missing files politely (one request a second). Returns messages for files that failed.

    football-data's fair-use policy asks for light automated downloads (at most twice a week): this only
    fetches files that aren't cached, so don't pass refresh=True routinely.
    """
    failed = []
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True) as client:
        for season in seasons:
            for code in codes:
                target = path(code, season)
                if target.exists() and not refresh:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                resp = client.get(URL.format(season=season, code=code))
                if resp.status_code != 200 or not resp.content.strip():
                    failed.append(f"{season}/{code}: HTTP {resp.status_code}")
                else:
                    target.write_bytes(resp.content)
                time.sleep(pause)
    return failed


def _read(file: Path) -> pd.DataFrame:
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            return pd.read_csv(file, encoding=encoding, on_bad_lines="skip")
        except UnicodeDecodeError:
            continue
    raise ValueError(f"cannot decode {file}")


def season_label(season: str) -> str:
    return f"20{season[:2]}/{season[2:]}"


def load(code: str, seasons=SEASONS) -> pd.DataFrame:
    """One league's matches across seasons, sorted by date, with standard columns."""
    frames = []
    for season in seasons:
        file = path(code, season)
        if not file.exists():
            continue
        raw = _read(file)
        raw = raw.dropna(subset=["HomeTeam", "AwayTeam", "FTHG", "FTAG"])
        if raw.empty:
            continue
        df = pd.DataFrame({
            "league": code,
            "season": season_label(season),
            "date": pd.to_datetime(raw["Date"], dayfirst=True, format="mixed"),
            "home": raw["HomeTeam"].astype(str).str.strip(),
            "away": raw["AwayTeam"].astype(str).str.strip(),
            "hg": raw["FTHG"].astype(int),
            "ag": raw["FTAG"].astype(int),
        })
        for name, candidates in COLUMNS.items():
            col = next((c for c in candidates if c in raw.columns), None)
            df[name] = pd.to_numeric(raw[col], errors="coerce").values if col else np.nan
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values(["date", "home"]).reset_index(drop=True)
