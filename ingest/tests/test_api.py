import httpx
import pytest

from engrebodds_ingest.api import ApiError, ApiFootball, QuotaExhausted


def client(handler, **kw):
    return ApiFootball("test-key", transport=httpx.MockTransport(handler), **kw)


def test_sends_key_and_tracks_quota():
    def handler(req):
        assert req.headers["x-apisports-key"] == "test-key"
        assert req.url.params["date"] == "2026-10-21"
        return httpx.Response(
            200,
            json={"errors": [], "response": [1, 2]},
            headers={"x-ratelimit-requests-remaining": "97"},
        )

    api = client(handler)
    assert api.get("/fixtures", date="2026-10-21")["response"] == [1, 2]
    assert api.requests_made == 1
    assert api.quota_remaining == 97


def test_body_errors_raise():
    api = client(lambda req: httpx.Response(200, json={"errors": {"plan": "Free plans do not have access"}}))
    with pytest.raises(ApiError) as exc:
        api.get("/fixtures")
    assert "plan" in exc.value.errors


def test_missing_key():
    with pytest.raises(ValueError):
        ApiFootball("")


def test_waits_on_rate_limit(monkeypatch):
    calls, slept = [], []
    monkeypatch.setattr("engrebodds_ingest.api.time.sleep", slept.append)

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(200, json={"errors": {"rateLimit": "Too many requests."}})
        return httpx.Response(200, json={"errors": [], "response": ["ok"]})

    assert client(handler).get("/odds", fixture=1)["response"] == ["ok"]
    assert len(calls) == 2 and slept == [61]


def test_get_all_follows_pages():
    def handler(req):
        page = int(req.url.params.get("page", 1))
        assert req.url.params["date"] == "2026-10-22"
        return httpx.Response(200, json={"errors": [], "paging": {"current": page, "total": 3}, "response": [page]})

    api = client(handler)
    assert api.get_all("/odds", date="2026-10-22") == [1, 2, 3]
    assert api.requests_made == 3


def test_quota_floor_stops_batch_jobs():
    def handler(req):
        return httpx.Response(200, json={"errors": [], "response": []},
                              headers={"x-ratelimit-requests-remaining": "400"})

    api = client(handler, quota_floor=500)
    api.get("/status")                      # first call learns the quota
    with pytest.raises(QuotaExhausted):
        api.get("/fixtures")
    assert api.requests_made == 1


def test_pauses_when_minute_allowance_used(monkeypatch):
    slept = []
    monkeypatch.setattr("engrebodds_ingest.api.time.sleep", slept.append)
    remaining = iter(["0", "9"])

    def handler(req):
        return httpx.Response(200, json={"errors": [], "response": []},
                              headers={"x-ratelimit-remaining": next(remaining)})

    api = client(handler)
    api.get("/a")
    api.get("/b")
    assert slept == [61]


def test_http_429_waits_out_the_minute(monkeypatch):
    slept, calls = [], []
    monkeypatch.setattr("engrebodds_ingest.api.time.sleep", slept.append)

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429)
        return httpx.Response(200, json={"errors": [], "response": ["ok"]})

    assert client(handler).get("/fixtures/statistics", fixture=1)["response"] == ["ok"]
    assert slept == [61]


def test_results_fallback_skips_dates_the_plan_refuses():
    from datetime import date
    from engrebodds_ingest.jobs.sync_results import fetch_due

    def handler(req):
        if "ids" in req.url.params:
            return httpx.Response(200, json={"errors": {"plan": "Free plans do not have access to the Ids parameter."}})
        if req.url.params["date"] == "2026-10-21":
            return httpx.Response(200, json={"errors": {"plan": "Free plans do not have access to this date."}})
        return httpx.Response(200, json={"errors": [], "paging": {"current": 1, "total": 1},
                                         "response": [{"fixture": {"id": 2}}, {"fixture": {"id": 99}}]})

    notes = []
    due = [(1, date(2026, 10, 21)), (2, date(2026, 10, 23))]
    batches = list(fetch_due(client(handler), due, notes))
    assert batches == [[{"fixture": {"id": 2}}]]          # the refused day is skipped, the allowed day kept
    assert any("2026-10-21: date refused" in n for n in notes)
