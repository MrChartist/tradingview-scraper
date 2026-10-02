"""Synchronous REST client. Live streaming lives in live.py."""
import random
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import requests

from .errors import (AuthError, ConnectionFailed, MarketApiError, NotFoundError,
                     RateLimitError, UpstreamError)

RETRY_STATUSES = {429, 502, 503, 504}
Symbols = Union[str, Iterable[str]]


def _csv(symbols: Symbols) -> str:
    return symbols if isinstance(symbols, str) else ",".join(symbols)


def _error_from(resp: requests.Response) -> MarketApiError:
    try:
        err = resp.json().get("error", {})
    except ValueError:
        err = {}
    message = err.get("message") or resp.text[:200] or f"HTTP {resp.status_code}"
    retry_after = None
    try:
        retry_after = float(resp.headers.get("Retry-After", ""))
    except ValueError:
        pass
    kwargs = dict(status=resp.status_code, code=err.get("code", "error"),
                  request_id=err.get("request_id") or resp.headers.get("X-Request-ID"), retry_after=retry_after)
    cls = {401: AuthError, 403: AuthError, 404: NotFoundError, 429: RateLimitError,
           502: UpstreamError, 503: UpstreamError, 504: UpstreamError}.get(resp.status_code, MarketApiError)
    return cls(message, **kwargs)


class MarketClient:
    """Client for the Open Market Terminal API.

    >>> client = MarketClient("https://api.example.com", api_key="...")
    >>> client.quotes(["NSE:RELIANCE", "NASDAQ:AAPL"])

    Transient failures (429, 502, 503, 504 and network errors) are retried with backoff,
    honouring the server's Retry-After. Everything else raises a typed MarketApiError.
    """

    def __init__(self, base_url: str = "http://localhost:8000", api_key: Optional[str] = None, *,
                 timeout: float = 30, max_retries: int = 3, session: Optional[requests.Session] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self.session.headers.update({"Accept": "application/json", "User-Agent": "open-market-client/1.0"})
        if api_key:
            self.session.headers["X-API-Key"] = api_key

    # ── plumbing ─────────────────────────────────────────────────
    def _request(self, method: str, path: str, *, params: Optional[dict] = None, json: Any = None) -> dict:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.request(method, f"{self.base_url}{path}", params=params, json=json, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as e:
                if attempt >= self.max_retries:
                    raise ConnectionFailed(f"Could not reach {self.base_url}: {e}") from e
                time.sleep(self._backoff(attempt))
                continue
            if resp.ok:
                return resp.json()
            err = _error_from(resp)
            if resp.status_code in RETRY_STATUSES and attempt < self.max_retries:
                time.sleep(err.retry_after if err.retry_after is not None else self._backoff(attempt))
                continue
            raise err
        raise MarketApiError("unreachable")  # pragma: no cover

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(0.5 * 2 ** attempt, 8) + random.random() * 0.25

    def _get(self, path: str, params: Optional[dict] = None, with_meta: bool = False):
        body = self._request("GET", path, params=params)
        return (body["data"], body["meta"]) if with_meta else body["data"]

    # ── meta ─────────────────────────────────────────────────────
    def health(self) -> dict:
        return self._request("GET", "/v1/health")

    def status(self) -> dict:
        return self._get("/v1/status")

    # ── quotes & symbols ─────────────────────────────────────────
    def quotes(self, symbols: Symbols, with_meta: bool = False):
        """Latest quote for up to 100 symbols like 'NSE:RELIANCE'. With `with_meta`,
        returns (quotes, meta) and meta['not_found'] lists unknown symbols."""
        return self._get("/v1/quotes", {"symbols": _csv(symbols)}, with_meta)

    def quote(self, symbol: str) -> dict:
        data, meta = self.quotes([symbol], with_meta=True)
        if not data:
            raise NotFoundError(f"Symbol not found: {symbol}", status=404, code="not_found")
        return data[0]

    def search(self, query: str, limit: int = 10) -> List[dict]:
        return self._get("/v1/symbols/search", {"q": query, "limit": limit})

    @staticmethod
    def _sym(symbol: str) -> str:
        if ":" not in symbol:
            raise ValueError("Use EXCHANGE:TICKER, for example 'NSE:RELIANCE'")
        exchange, ticker = symbol.split(":", 1)
        return f"/v1/symbols/{exchange}/{ticker}"

    def symbol(self, symbol: str) -> dict:
        """Profile and key statistics. Monetary values are in the listing currency (see data['currency'])."""
        return self._get(self._sym(symbol))

    def fundamentals(self, symbol: str) -> dict:
        return self._get(f"{self._sym(symbol)}/fundamentals")

    def technicals(self, symbol: str, timeframe: str = "1d") -> dict:
        return self._get(f"{self._sym(symbol)}/technicals", {"timeframe": timeframe})

    def candles(self, symbol: str, timeframe: str = "1d", limit: int = 100, as_dataframe: bool = False):
        """Oldest-first OHLCV. `time` is epoch seconds (UTC). Set as_dataframe=True for pandas."""
        data = self._get(f"{self._sym(symbol)}/candles", {"timeframe": timeframe, "limit": limit})
        if not as_dataframe:
            return data
        import pandas as pd  # optional dependency
        df = pd.DataFrame(data)
        df["datetime"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return df.set_index("datetime")[["open", "high", "low", "close", "volume"]]

    def news(self, symbol: str, limit: int = 20, language: str = "en") -> List[dict]:
        return self._get(f"{self._sym(symbol)}/news", {"limit": limit, "language": language})

    # ── markets ──────────────────────────────────────────────────
    def movers(self, market: str = "stocks-india", category: str = "gainers", limit: int = 25) -> List[dict]:
        return self._get("/v1/markets/movers", {"market": market, "category": category, "limit": limit})

    def screener(self, market: str = "india", conditions: Optional[Sequence[Dict[str, Any]]] = None, *,
                 columns: Optional[Sequence[str]] = None, sort_by: str = "volume", sort_order: str = "desc",
                 limit: int = 25, main_only: bool = True, with_meta: bool = False):
        """conditions: [{'field': 'market_cap_basic', 'op': 'gte', 'value': 5e11}, ...]
        ops: gt, gte, lt, lte, eq, neq, in, between."""
        body = {"market": market, "conditions": list(conditions or []), "columns": list(columns) if columns else None,
                "sort_by": sort_by, "sort_order": sort_order, "limit": limit, "main_only": main_only}
        resp = self._request("POST", "/v1/screener", json=body)
        return (resp["data"], resp["meta"]) if with_meta else resp["data"]

    # ── calendar ─────────────────────────────────────────────────
    def earnings(self, markets: Union[str, Iterable[str]] = "india", date_from: Optional[str] = None,
                 date_to: Optional[str] = None, limit: int = 100) -> List[dict]:
        return self._get("/v1/calendar/earnings", {"markets": _csv(markets), "from": date_from, "to": date_to, "limit": limit})

    def dividends(self, markets: Union[str, Iterable[str]] = "india", date_from: Optional[str] = None,
                  date_to: Optional[str] = None, limit: int = 100) -> List[dict]:
        return self._get("/v1/calendar/dividends", {"markets": _csv(markets), "from": date_from, "to": date_to, "limit": limit})

    # ── live ─────────────────────────────────────────────────────
    def live(self, symbols: Optional[Symbols] = None):
        """Returns a LiveSession (async). See open_market_client.live."""
        from .live import LiveSession
        return LiveSession(self.base_url, self.api_key, symbols)

    def stream(self, symbols: Symbols):
        """Async iterator of quote dicts that reconnects automatically:  async for q in client.stream([...])"""
        return self.live(symbols).quotes()

    def close(self) -> None:
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
