"""Public proof of the record: commit and reveal (docs: Phase 4 plan, addition 8).

A live publish run stores the canonical JSON of exactly what it published (record_run, inside the publish
transaction). After that transaction commits, the JSON's SHA-256 is posted to a public Telegram channel, whose
server timestamps can't be backdated, and committed to the public record repo (post_pending). Once every tip in
the run is graded, the JSON itself is committed and posted (reveal_due), so anyone can check that its SHA-256
matches the hash posted before kick-off. Preview runs never get here.

Configuration (GitHub secrets / env): TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (e.g. @engrebodds_record),
RECORD_REPO (owner/name), RECORD_REPO_TOKEN (fine-grained token, contents: read and write on that repo only).
Anything missing is skipped with a note; publishing never depends on it.
"""

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

TELEGRAM = "https://api.telegram.org"
GITHUB = "https://api.github.com"


@dataclass(frozen=True)
class ProofConfig:
    telegram_token: str | None
    telegram_chat: str | None
    repo: str | None
    repo_token: str | None

    @classmethod
    def from_env(cls) -> "ProofConfig":
        get = lambda k: (os.environ.get(k) or "").strip() or None  # noqa: E731
        return cls(get("TELEGRAM_BOT_TOKEN"), get("TELEGRAM_CHAT_ID"), get("RECORD_REPO"), get("RECORD_REPO_TOKEN"))

    @property
    def telegram(self) -> bool:
        return bool(self.telegram_token and self.telegram_chat)

    @property
    def github(self) -> bool:
        return bool(self.repo and self.repo_token)


# ---------- the document ----------

def canonical(doc: dict) -> str:
    """Stable text for a document: sorted keys, no spaces, numbers already formatted as strings."""
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _num(v, places: int) -> str | None:
    return None if v is None else f"{float(v):.{places}f}"


def _ts(v: datetime | None) -> str | None:
    if v is None:
        return None
    if v.tzinfo is None:
        v = v.replace(tzinfo=UTC)
    return v.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


RUN_TIPS_SQL = """
select t.id, f.api_fixture_id, t.kickoff_at, lg.name, ht.name, aw.name, t.slot, t.model_prob, t.fair_odds, t.ref_odds,
       b.name, t.ref_captured_at, t.is_free, t.is_banker, t.locks_at,
       (select json_agg(json_build_array(l.market_code, l.selection, l.line) order by l.leg_no)
          from public.tip_legs l where l.tip_id = t.id)
  from public.tips t
  join public.fixtures f on f.id = t.fixture_id
  join public.leagues lg on lg.id = f.league_id
  join public.teams ht on ht.id = f.home_team_id
  join public.teams aw on aw.id = f.away_team_id
  left join public.bookmakers b on b.id = t.ref_bookmaker_id
 where t.published_at = %(at)s
 order by t.id
"""
RUN_SLIPS_SQL = """
select s.kind, s.slip_date, s.ref_odds, s.combined_prob, s.locks_at,
       (select array_agg(sl.tip_id order by sl.tip_id) from public.slip_legs sl where sl.slip_id = s.id)
  from public.slips s
 where s.published_at = %(at)s
 order by s.kind
"""


def build_document(conn, published_at: datetime, window_date) -> dict | None:
    tips = conn.execute(RUN_TIPS_SQL, {"at": published_at}).fetchall()
    slips = conn.execute(RUN_SLIPS_SQL, {"at": published_at}).fetchall()
    if not tips and not slips:
        return None
    return {
        "site": "engrebodds",
        "format": 1,
        "published_at": _ts(published_at),
        "window_date": str(window_date),
        "tips": [{
            "tip_id": tid, "fixture": api_id, "kickoff_utc": _ts(ko), "league": league, "home": home, "away": away,
            "slot": slot, "legs": [[m, s, _num(line, 1)] for m, s, line in legs], "model_prob": _num(p, 4),
            "fair_odds": _num(fair, 2), "odds": _num(odds, 2), "bookmaker": bk, "odds_captured_at": _ts(cap),
            "free": free, "banker": banker, "locks_at": _ts(locks),
        } for tid, api_id, ko, league, home, away, slot, p, fair, odds, bk, cap, free, banker, locks, legs in tips],
        "slips": [{"kind": kind, "date": str(day), "odds": _num(odds, 2), "chance_all_land": _num(chance, 5),
                   "locks_at": _ts(locks), "tip_ids": list(ids or [])}
                  for kind, day, odds, chance, locks, ids in slips],
    }


def record_run(conn, window_date) -> dict | None:
    """Inside the publish transaction: store what this transaction published, with its hash."""
    published_at = conn.execute("select now()").fetchone()[0]
    doc = build_document(conn, published_at, window_date)
    if doc is None:
        return None
    text = canonical(doc)
    kickoffs = [t["kickoff_utc"] for t in doc["tips"]] or [s["locks_at"] for s in doc["slips"]]
    row = {"published_at": published_at, "window_date": window_date, "tips": len(doc["tips"]), "slips": len(doc["slips"]),
           "first_kickoff": min(kickoffs), "last_kickoff": max(kickoffs), "payload": text, "sha256": digest(text)}
    conn.execute("""insert into public.proof_runs (published_at, window_date, tips, slips, first_kickoff, last_kickoff,
                        payload, sha256) values (%(published_at)s, %(window_date)s, %(tips)s, %(slips)s,
                        %(first_kickoff)s, %(last_kickoff)s, %(payload)s, %(sha256)s)
                    on conflict (published_at) do nothing""", row)   # one publish per transaction; a repeat adds nothing
    return row


# ---------- posting ----------

def file_stem(published_at: datetime, window_date) -> str:
    return f"{window_date}/{_ts(published_at)[11:19].replace(':', '')}"   # e.g. 2026-10-25/064512


def _telegram(http: httpx.Client, cfg: ProofConfig, method: str, **kw) -> dict:
    r = http.post(f"{TELEGRAM}/bot{cfg.telegram_token}/{method}", **kw)
    body = r.json()
    if not body.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {body.get('description', r.status_code)}")
    return body["result"]


def _github_put(http: httpx.Client, cfg: ProofConfig, path: str, content: str, message: str) -> str:
    r = http.put(f"{GITHUB}/repos/{cfg.repo}/contents/{path}",
                 headers={"Authorization": f"Bearer {cfg.repo_token}", "Accept": "application/vnd.github+json"},
                 json={"message": message, "content": base64.b64encode(content.encode("utf-8")).decode()})
    if r.status_code not in (200, 201):
        raise RuntimeError(f"GitHub commit of {path} failed: {r.status_code} {r.text[:200]}")
    return r.json()["content"]["html_url"]


PENDING_SQL = """
select id, published_at, window_date, tips, slips, first_kickoff, sha256, telegram_posted_at, repo_committed_at
  from public.proof_runs
 where missed is null and (telegram_posted_at is null or repo_committed_at is null)
 order by published_at
"""


def post_pending(conn, cfg: ProofConfig, http: httpx.Client, now: datetime | None = None, commit=None) -> list[str]:
    """Post the hashes not yet posted. A hash first posted after kick-off proves nothing, so it is marked missed.

    Each row is committed as soon as it's posted (commit defaults to conn.commit), so a message that went out is
    never forgotten if a later one fails.
    """
    now = now or datetime.now(UTC)
    commit = commit or conn.commit
    notes = []
    for rid, at, day, tips, slips, first_ko, sha, tg_at, repo_at in conn.execute(PENDING_SQL).fetchall():
        stem = file_stem(at, day)
        if tg_at is None and now >= first_ko:
            conn.execute("update public.proof_runs set missed = %s where id = %s",
                         ("hash not posted to Telegram before the first kick-off", rid))
            notes.append(f"proof {stem}: MISSED (not posted before kick-off)")
            continue
        if not (cfg.telegram or cfg.github):
            notes.append(f"proof {stem}: not configured, hash stored only")
            continue
        try:
            if cfg.telegram and tg_at is None:
                text = (f"Engrebodds tips for {day} (published {_ts(at)[11:16]} UTC): {tips} tips, {slips} slips.\n"
                        f"SHA-256: {sha}\n"
                        "The full list is posted here after the last match; its SHA-256 must equal this.")
                msg = _telegram(http, cfg, "sendMessage", json={"chat_id": cfg.telegram_chat, "text": text})
                conn.execute("update public.proof_runs set telegram_message_id = %s, telegram_posted_at = now() where id = %s",
                             (msg["message_id"], rid))
            if cfg.github and repo_at is None:
                url = _github_put(http, cfg, f"hashes/{stem}.sha256", f"{sha}  {stem.replace('/', '-')}.json\n",
                                  f"Hash of tips published {_ts(at)}")
                conn.execute("update public.proof_runs set repo_hash_url = %s, repo_committed_at = now() where id = %s",
                             (url, rid))
            notes.append(f"proof {stem}: hash posted")
        except (httpx.HTTPError, RuntimeError) as e:
            notes.append(f"proof {stem}: posting failed, will retry before kick-off ({e})")
        commit()
    return notes


DUE_SQL = """
select r.id, r.published_at, r.window_date, r.payload, r.sha256, r.telegram_message_id
  from public.proof_runs r
 where r.revealed_at is null
   and (r.telegram_posted_at is not null or r.repo_committed_at is not null or r.missed is not null)
   and not exists (select 1 from public.tips t where t.published_at = r.published_at and t.result = 'pending')
   and not exists (select 1 from public.slips s where s.published_at = r.published_at and s.result = 'pending')
 order by r.published_at
"""


def reveal_due(conn, cfg: ProofConfig, http: httpx.Client, commit=None) -> list[str]:
    """Publish the full JSON of runs whose tips and slips are all graded."""
    commit = commit or conn.commit
    notes = []
    for rid, at, day, payload, sha, msg_id in conn.execute(DUE_SQL).fetchall():
        stem = file_stem(at, day)
        if digest(payload) != sha:                       # can't happen (the guard trigger), but never reveal a mismatch
            notes.append(f"proof {stem}: payload does not match its hash; not revealed")
            continue
        if not (cfg.telegram or cfg.github):
            notes.append(f"proof {stem}: graded, not configured to reveal")
            continue
        try:
            url = None
            if cfg.github:
                url = _github_put(http, cfg, f"runs/{stem}.json", payload, f"Tips published {_ts(at)}, revealed after grading")
            if cfg.telegram:
                caption = f"Engrebodds tips for {day}, all graded. SHA-256 {sha}" + (f"\n{url}" if url else "")
                _telegram(http, cfg, "sendDocument",
                          data={"chat_id": cfg.telegram_chat, "caption": caption[:1024],
                                **({"reply_to_message_id": str(msg_id)} if msg_id else {})},
                          files={"document": (f"{stem.replace('/', '-')}.json", payload.encode("utf-8"), "application/json")})
            conn.execute("update public.proof_runs set revealed_at = now(), reveal_url = %s where id = %s", (url, rid))
            notes.append(f"proof {stem}: revealed")
        except (httpx.HTTPError, RuntimeError) as e:
            notes.append(f"proof {stem}: reveal failed, will retry ({e})")
        commit()
    return notes


def run(settings, **_) -> None:
    """Scheduled job: post any pending hashes, then reveal graded runs."""
    from engrebodds_ingest.db import job_run
    cfg = ProofConfig.from_env()
    with job_run(settings, "proof") as ctx, httpx.Client(timeout=30) as http:
        ctx.notes += post_pending(ctx.conn, cfg, http)
        ctx.notes += reveal_due(ctx.conn, cfg, http)
        if not ctx.notes:
            ctx.notes.append("nothing to post or reveal")
