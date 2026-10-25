"""Proof of record: canonical hashing, posting before kick-off, reveal after grading (DB parts rolled back)."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import psycopg
import pytest

from engrebodds_ingest.config import load_settings
from engrebodds_model.live import proof
from engrebodds_model.live.proof import ProofConfig, canonical, digest, file_stem
from engrebodds_model.live.publish import _preview, _publish, plan
from test_publish_db import pick_now, setup_league


def test_canonical_text_ignores_key_order_and_hashes_stably():
    a = canonical({"b": [1, {"y": "2", "x": None}], "a": "₦"})
    b = canonical({"a": "₦", "b": [1, {"x": None, "y": "2"}]})
    assert a == b == '{"a":"₦","b":[1,{"x":null,"y":"2"}]}'
    assert digest(a) == digest(b) and len(digest(a)) == 64
    assert file_stem(datetime(2026, 10, 25, 6, 45, 12, tzinfo=UTC), "2026-10-25") == "2026-10-25/064512"


def test_config_from_env(monkeypatch):
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "RECORD_REPO", "RECORD_REPO_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    assert not ProofConfig.from_env().telegram and not ProofConfig.from_env().github
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "@c")
    assert ProofConfig.from_env().telegram and not ProofConfig.from_env().github


# ---------- against the local database ----------

@pytest.fixture
def conn():
    try:
        c = psycopg.connect(load_settings().database_url, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("local Supabase is not running")
    yield c
    c.rollback()
    c.close()


class FakeApis:
    """Records Telegram and GitHub calls; answers like the real APIs."""

    def __init__(self, fail_telegram=False):
        self.calls, self.files, self.fail_telegram = [], {}, fail_telegram

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append((request.method, url))
        if "api.telegram.org" in url:
            if self.fail_telegram:
                return httpx.Response(200, json={"ok": False, "description": "chat not found"})
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 41 + len(self.calls)}})
        path = url.split("/contents/", 1)[1]
        body = json.loads(request.content)
        import base64
        self.files[path] = base64.b64decode(body["content"]).decode("utf-8")
        return httpx.Response(201, json={"content": {"html_url": f"https://github.com/o/r/blob/main/{path}"}})


CFG = ProofConfig("token", "@engrebodds_record", "o/r", "repo-token")


def published(conn):
    now = pick_now()
    _, fids = setup_league(conn, now=now)
    _publish(conn, plan(conn, now, preview_all=False), now, [])
    return fids, conn.execute("select * from public.proof_runs order by id desc limit 1").fetchone()


def test_live_publish_stores_the_exact_published_tips_with_their_hash(conn):
    fids, row = published(conn)
    run = conn.execute("select payload, sha256, tips, slips from public.proof_runs order by id desc limit 1").fetchone()
    payload, sha, tips, slips = run
    assert digest(payload) == sha
    doc = json.loads(payload)
    assert tips == len(doc["tips"]) == 8 and slips == len(doc["slips"]) >= 1
    db_tips = conn.execute("select id from public.tips where fixture_id = any(%s) order by id", (fids,)).fetchall()
    assert [t["tip_id"] for t in doc["tips"]] == [r[0] for r in db_tips]
    first = doc["tips"][0]
    assert first["legs"] and first["model_prob"].count(".") == 1 and first["odds_captured_at"].endswith("Z")
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("update public.proof_runs set payload = payload || ' ' where sha256 = %s", (sha,))


def test_preview_never_records_a_proof(conn):
    now = pick_now()
    setup_league(conn, now=now)
    before = conn.execute("select count(*) from public.proof_runs").fetchone()[0]
    _preview(conn, plan(conn, now, preview_all=False), [])
    assert conn.execute("select count(*) from public.proof_runs").fetchone()[0] == before


def test_hash_is_posted_before_kickoff_then_revealed_after_grading(conn):
    fids, _ = published(conn)
    sha = conn.execute("select sha256 from public.proof_runs order by id desc limit 1").fetchone()[0]
    apis = FakeApis()
    with httpx.Client(transport=httpx.MockTransport(apis)) as http:
        notes = proof.post_pending(conn, CFG, http, commit=lambda: None)
        assert any("hash posted" in n for n in notes)
        tg_text = [c for c in apis.calls if "sendMessage" in c[1]]
        assert tg_text and any(p.startswith("hashes/") and sha in v for p, v in apis.files.items())
        assert proof.reveal_due(conn, CFG, http, commit=lambda: None) == []          # nothing graded yet

        # Grade everything published in the run, then the full JSON goes out and matches the posted hash.
        conn.execute("update public.fixtures set status = 'FT', home_90 = 2, away_90 = 0, home_ht = 1, away_ht = 0 "
                     "where id = any(%s)", (fids,))
        conn.execute("select public.grade_all(now() + interval '1 day')")
        conn.execute("update public.slips set result = 'won', graded_at = now() where result = 'pending' and published_at = "
                     "(select published_at from public.proof_runs order by id desc limit 1)")
        notes = proof.reveal_due(conn, CFG, http, commit=lambda: None)
    assert any("revealed" in n for n in notes)
    revealed = [v for p, v in apis.files.items() if p.startswith("runs/")]
    assert revealed and digest(revealed[0]) == sha
    assert any("sendDocument" in c[1] for c in apis.calls)


def test_a_hash_not_posted_before_kickoff_is_marked_missed_never_posted_late(conn):
    published(conn)
    apis = FakeApis()
    with httpx.Client(transport=httpx.MockTransport(apis)) as http:
        later = datetime.now(UTC) + timedelta(days=2)
        notes = proof.post_pending(conn, CFG, http, now=later, commit=lambda: None)
    assert any("MISSED" in n for n in notes) and apis.calls == []
    assert conn.execute("select missed from public.proof_runs order by id desc limit 1").fetchone()[0]


def test_unconfigured_or_failing_posts_leave_the_hash_pending(conn):
    published(conn)
    with httpx.Client(transport=httpx.MockTransport(FakeApis())) as http:
        notes = proof.post_pending(conn, ProofConfig(None, None, None, None), http, commit=lambda: None)
    assert any("not configured" in n for n in notes)
    with httpx.Client(transport=httpx.MockTransport(FakeApis(fail_telegram=True))) as http:
        notes = proof.post_pending(conn, ProofConfig("t", "@c", None, None), http, commit=lambda: None)
    assert any("will retry" in n for n in notes)
    assert conn.execute("select telegram_posted_at, missed from public.proof_runs order by id desc limit 1").fetchone() == (None, None)
