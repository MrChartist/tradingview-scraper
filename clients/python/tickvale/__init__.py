"""Python client for the Tickvale API."""
from .client import MarketClient, TickvaleClient
from .errors import (AuthError, ConnectionFailed, MarketApiError, NotFoundError,
                     RateLimitError, UpstreamError)
from .live import LiveSession
from .ws import Socket

__all__ = ["TickvaleClient", "Socket", "MarketClient", "LiveSession", "MarketApiError", "AuthError", "RateLimitError",
           "NotFoundError", "UpstreamError", "ConnectionFailed"]
__version__ = "1.0.0"
