"""Live quotes over WebSocket with automatic reconnect and re-subscribe."""
import asyncio
import json
import random
from typing import AsyncIterator, Iterable, Optional, Set, Union

from .errors import AuthError, ConnectionFailed, MarketApiError, RateLimitError


def _ws_url(base_url: str) -> str:
    return base_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/v1/ws"


class LiveSession:
    """
    async with client.live(["NASDAQ:AAPL", "BINANCE:BTCUSDT"]) as live:
        async for quote in live.quotes():
            print(quote["symbol"], quote["price"], "delayed" if quote["delayed"] else "realtime")

    Subscriptions are remembered, so after a dropped connection the session reconnects
    (exponential backoff) and subscribes again. Authentication errors are not retried.
    """

    def __init__(self, base_url: str, api_key: Optional[str], symbols: Optional[Union[str, Iterable[str]]] = None,
                 *, reconnect: bool = True, max_backoff: float = 30):
        self.url = _ws_url(base_url)
        self.api_key = api_key
        self.reconnect = reconnect
        self.max_backoff = max_backoff
        self.symbols: Set[str] = set([symbols] if isinstance(symbols, str) else (symbols or []))
        self._ws = None
        self._closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def subscribe(self, symbols: Union[str, Iterable[str]]) -> None:
        new = {symbols} if isinstance(symbols, str) else set(symbols)
        self.symbols |= new
        await self._send({"action": "subscribe", "symbols": sorted(new)})

    async def unsubscribe(self, symbols: Union[str, Iterable[str]]) -> None:
        gone = {symbols} if isinstance(symbols, str) else set(symbols)
        self.symbols -= gone
        await self._send({"action": "unsubscribe", "symbols": sorted(gone)})

    async def _send(self, message: dict) -> None:
        if self._ws is not None:
            await self._ws.send(json.dumps(message))

    async def close(self) -> None:
        self._closed = True
        if self._ws is not None:
            await self._ws.close()

    async def quotes(self) -> AsyncIterator[dict]:
        """Yield quote dicts until closed. Raises AuthError/RateLimitError on fatal rejections."""
        try:
            from websockets.asyncio.client import connect
            from websockets.exceptions import ConnectionClosed
        except ImportError as e:  # pragma: no cover
            raise ImportError("Live streaming needs the 'websockets' package: pip install tickvale[live]") from e

        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        backoff = 1.0
        while not self._closed:
            try:
                async with connect(self.url, additional_headers=headers, open_timeout=15) as ws:
                    self._ws = ws
                    backoff = 1.0
                    if self.symbols:
                        await ws.send(json.dumps({"action": "subscribe", "symbols": sorted(self.symbols)}))
                    async for raw in ws:
                        msg = json.loads(raw)
                        kind = msg.get("type")
                        if kind == "quote":
                            yield msg["data"]
                        elif kind == "error":
                            code = msg.get("code")
                            if code == "unauthorized":
                                raise AuthError(msg.get("message", "Unauthorized"), status=401, code=code)
                            if code == "rate_limited":
                                raise RateLimitError(msg.get("message", "Rate limited"), status=429, code=code)
                            if code == "unavailable":
                                raise MarketApiError(msg.get("message", "Unavailable"), status=503, code=code)
            except (AuthError, RateLimitError):
                raise
            except (ConnectionClosed, OSError, asyncio.TimeoutError) as e:
                if self._closed:
                    return
                if not self.reconnect:
                    raise ConnectionFailed(f"Live connection lost: {e}") from e
            finally:
                self._ws = None
            if self._closed or not self.reconnect:
                return
            await asyncio.sleep(backoff + random.random() * 0.5)
            backoff = min(backoff * 2, self.max_backoff)
