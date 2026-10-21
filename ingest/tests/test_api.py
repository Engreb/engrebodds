import httpx
import pytest

from engrebodds_ingest.api import ApiError, ApiFootball


def client(handler):
    return ApiFootball("test-key", transport=httpx.MockTransport(handler))


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
