"""API-Football v3 client: auth, retries, rate limits, pagination and a daily quota floor."""

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


class QuotaExhausted(Exception):
    """The daily quota fell below the floor kept free for live jobs."""


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and (
        exc.response.status_code == 429 or exc.response.status_code >= 500
    )


class ApiFootball:
    """quota_floor: refuse to make a request while fewer than this many daily requests remain.
    Live jobs use 0; batch jobs (backfill) use Settings.quota_floor so they can't starve live jobs.
    """

    def __init__(self, api_key: str, *, quota_floor: int = 0, transport: httpx.BaseTransport | None = None):
        if not api_key:
            raise ValueError("API_FOOTBALL_KEY is not set (see .env.example)")
        self._http = httpx.Client(
            base_url=BASE_URL,
            headers={"x-apisports-key": api_key},
            timeout=30,
            transport=transport,
        )
        self.quota_floor = quota_floor
        self.requests_made = 0
        self.quota_remaining: int | None = None    # daily
        self.minute_remaining: int | None = None   # per minute

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

    def get_all(self, endpoint: str, **params) -> list:
        """Follow `paging` and return every page's `response` items concatenated."""
        body = self.get(endpoint, **params)
        items = list(body["response"])
        total = (body.get("paging") or {}).get("total", 1)
        for page in range(2, total + 1):
            items.extend(self.get(endpoint, **params, page=page)["response"])
        return items

    @retry(
        retry=retry_if_exception(_retryable),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, max=30),
        reraise=True,
    )
    def _get_once(self, endpoint: str, params: dict) -> dict:
        if self.quota_remaining is not None and self.quota_remaining < self.quota_floor:
            raise QuotaExhausted(f"{self.quota_remaining} requests left today, floor is {self.quota_floor}")
        if self.minute_remaining == 0:
            time.sleep(RATE_LIMIT_WAIT_S)
            self.minute_remaining = None

        resp = self._http.get(endpoint, params=params)
        self.requests_made += 1
        daily = resp.headers.get("x-ratelimit-requests-remaining")
        if daily is not None:
            self.quota_remaining = int(daily)
        minute = resp.headers.get("x-ratelimit-remaining")
        if minute is not None:
            self.minute_remaining = int(minute)
        resp.raise_for_status()
        body = resp.json()
        # "errors" is [] when fine, or a dict like {"plan": "..."} / {"token": "..."}.
        if body.get("errors"):
            raise ApiError(endpoint, body["errors"])
        return body

    def close(self) -> None:
        self._http.close()
