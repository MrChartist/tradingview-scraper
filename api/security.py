"""API-key authentication and per-client rate limiting for the product API."""
import hmac
import threading
import time
from typing import Optional, Tuple

from fastapi import Request, Response, WebSocket

from api.config import Settings
from api.errors import ApiError


def extract_key(headers, query_params=None) -> Optional[str]:
    """Key from `X-API-Key`, `Authorization: Bearer <key>`, or (for WebSockets/SSE) `?api_key=`."""
    key = headers.get("x-api-key")
    if key:
        return key.strip()
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    if query_params is not None:
        return query_params.get("api_key")
    return None


def match_key(settings: Settings, presented: Optional[str]) -> Optional[str]:
    """Return the client name for a valid key (constant-time comparison), else None."""
    if not presented:
        return None
    found = None
    for key, name in settings.api_keys.items():
        if hmac.compare_digest(key.encode(), presented.encode()):
            found = name
    return found


def client_ip(settings: Settings, headers, fallback: str) -> str:
    if settings.trust_proxy_headers:
        fwd = headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return fallback


class RateLimiter:
    """Fixed one-minute window per client. In-memory, so it is per process;
    run a single worker, or put a shared limiter (for example at the proxy) in front for many."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._lock = threading.Lock()
        self._windows: dict = {}

    def hit(self, client: str) -> Tuple[bool, int, int]:
        """Returns (allowed, remaining, seconds_until_reset)."""
        now = time.time()
        window = int(now // 60)
        with self._lock:
            if len(self._windows) > 10000:
                self._windows = {k: v for k, v in self._windows.items() if v[0] == window}
            w, count = self._windows.get(client, (window, 0))
            if w != window:
                w, count = window, 0
            count += 1
            self._windows[client] = (w, count)
        reset = int(60 - (now % 60)) or 1
        return count <= self.per_minute, max(0, self.per_minute - count), reset


class Guard:
    """FastAPI dependency: authenticates, rate limits and annotates the response."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.limiter = RateLimiter(settings.rate_limit_per_minute)

    def authenticate(self, headers, query_params, fallback_ip: str) -> str:
        s = self.settings
        presented = extract_key(headers, query_params)
        name = match_key(s, presented)
        if s.auth_enabled or s.require_api_key:
            if not presented:
                raise ApiError(401, "Missing API key. Send it as the X-API-Key header or 'Authorization: Bearer <key>'.")
            if name is None:
                raise ApiError(401, "Invalid API key.")
            return name
        return name or f"ip:{client_ip(s, headers, fallback_ip)}"

    async def http(self, request: Request, response: Response) -> str:
        who = self.authenticate(request.headers, request.query_params,
                                request.client.host if request.client else "unknown")
        allowed, remaining, reset = self.limiter.hit(who)
        request.state.client = who
        request.state.rate_headers = {
            "X-RateLimit-Limit": str(self.limiter.per_minute),
            "X-RateLimit-Remaining": str(remaining),
            "X-RateLimit-Reset": str(reset),
        }
        response.headers.update(request.state.rate_headers)
        if not allowed:
            raise ApiError(429, "Rate limit exceeded. Slow down or ask for a higher limit.",
                           headers={**request.state.rate_headers, "Retry-After": str(reset)})
        return who

    def websocket(self, ws: WebSocket) -> str:
        return self.authenticate(ws.headers, ws.query_params, ws.client.host if ws.client else "unknown")
