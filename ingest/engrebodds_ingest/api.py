"""Minimal API-Football v3 client: auth, retries, quota tracking.

Pagination and the quota floor for batch jobs arrive in step 4.
"""

import time

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

BASE_URL = "https://v3.football.api-sports.io"

# Per-minute limit (10/min on the free plan). The API reports it as a body error, not HTTP 429.
RATE_LIMIT_WAIT_S = 61
RATE_LIMIT_RETRIES = 3


class ApiError(Exception):
    """API-Football reported an error in the response body (it answers HTTP 200 for these)."""

    def __init__(self, endpoint: str, errors):
        super().__init__(f"{endpoint}: {errors}")
        self.endpoint = endpoint
        self.errors = errors


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and (
        exc.response.status_code == 429 or exc.response.status_code >= 500
    )


class ApiFootball:
    def __init__(self, api_key: str, *, transport: httpx.BaseTransport | None = None):
        if not api_key:
            raise ValueError("API_FOOTBALL_KEY is not set (see .env.example)")
        self._http = httpx.Client(
            base_url=BASE_URL,
            headers={"x-apisports-key": api_key},
            timeout=30,
            transport=transport,
        )
        self.requests_made = 0
        self.quota_remaining: int | None = None

    def get(self, endpoint: str, **params) -> dict:
        """GET an endpoint and return the full JSON body. Raises ApiError on body errors.

        Waits and retries when the per-minute rate limit is hit.
        """
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            try:
                return self._get_once(endpoint, params)
            except ApiError as e:
                if not (isinstance(e.errors, dict) and "rateLimit" in e.errors) or attempt == RATE_LIMIT_RETRIES:
                    raise
                time.sleep(RATE_LIMIT_WAIT_S)
        raise AssertionError("unreachable")

    @retry(
        retry=retry_if_exception(_retryable),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, max=30),
        reraise=True,
    )
    def _get_once(self, endpoint: str, params: dict) -> dict:
        resp = self._http.get(endpoint, params=params)
        self.requests_made += 1
        remaining = resp.headers.get("x-ratelimit-requests-remaining")
        if remaining is not None:
            self.quota_remaining = int(remaining)
        resp.raise_for_status()
        body = resp.json()
        # "errors" is [] when fine, or a dict like {"plan": "..."} / {"token": "..."}.
        if body.get("errors"):
            raise ApiError(endpoint, body["errors"])
        return body

    def close(self) -> None:
        self._http.close()
