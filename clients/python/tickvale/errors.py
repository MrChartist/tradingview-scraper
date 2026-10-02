from typing import Optional


class MarketApiError(Exception):
    """Any error returned by (or while reaching) the API."""

    def __init__(self, message: str, *, status: Optional[int] = None, code: str = "error",
                 request_id: Optional[str] = None, retry_after: Optional[float] = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.request_id = request_id
        self.retry_after = retry_after

    def __str__(self) -> str:
        bits = [self.message]
        if self.status:
            bits.append(f"[{self.status} {self.code}]")
        if self.request_id:
            bits.append(f"(request {self.request_id})")
        return " ".join(bits)


class AuthError(MarketApiError):
    """Missing or invalid API key (401/403)."""


class RateLimitError(MarketApiError):
    """Rate limit hit (429). `retry_after` says how many seconds to wait."""


class NotFoundError(MarketApiError):
    """Symbol or resource not found (404)."""


class UpstreamError(MarketApiError):
    """The market data source failed (502/503/504). Usually worth retrying."""


class ConnectionFailed(MarketApiError):
    """Could not reach the API at all."""
