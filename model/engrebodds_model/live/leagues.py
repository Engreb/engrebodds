"""Switch a league's published tips on or off (manual, after reviewing shadow evidence), and set priorities."""

import json

import psycopg

from engrebodds_model.live import shadow
from engrebodds_model.live.publish import NPFL_API_ID


def _league(conn, api_league_id: int):
    row = conn.execute("select id, name, country from public.leagues where api_league_id = %s",
                       (api_league_id,)).fetchone()
    if not row:
        raise ValueError(f"no league with API-Football id {api_league_id}")
    return row


def league_tips(conn: psycopg.Connection, api_league_id: int, enable: bool) -> dict:
    """Turn tips on (only if the league passes the shadow gate) or off. Returns the evidence and the outcome."""
    league_id, name, country = _league(conn, api_league_id)
    evidence = shadow.league_evidence(conn, league_id)
    if enable:
        if api_league_id == NPFL_API_ID:
            return {"league": name, "changed": False, "refused": "NPFL tips are not offered yet", "evidence": evidence}
        if country == "World":
            return {"league": name, "changed": False, "refused": "continental competitions get no tips in v1",
                    "evidence": evidence}
        if not evidence["pass"]:
            return {"league": name, "changed": False, "refused": "; ".join(evidence["reasons"]), "evidence": evidence}
        conn.execute("update public.leagues set tips_enabled = true, tips_enabled_at = now() where id = %s", (league_id,))
    else:
        conn.execute("update public.leagues set tips_enabled = false where id = %s", (league_id,))
    return {"league": name, "changed": True, "tips_enabled": enable, "evidence": evidence}


def league_priority(conn: psycopg.Connection, api_league_id: int, priority: int) -> str:
    """Free-tip priority (lower comes first; leagues without one come last)."""
    league_id, name, _ = _league(conn, api_league_id)
    conn.execute("update public.leagues set display_order = %s where id = %s", (priority, league_id))
    return name


def describe(result: dict) -> str:
    head = (f"{result['league']}: tips {'ON' if result.get('tips_enabled') else 'OFF'}" if result["changed"]
            else f"{result['league']}: NOT changed ({result['refused']})")
    return head + "\nEvidence:\n" + json.dumps(result["evidence"], indent=2, default=str)
