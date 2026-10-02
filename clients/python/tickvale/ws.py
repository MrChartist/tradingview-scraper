"""WebSocket client: ask for anything and receive live data over one connection.

    async with client.socket() as sock:
        print(await sock.call("quotes", symbols=["reliance", "bitcoin"]))      # plain names work
        await sock.subscribe("quotes", symbols=["bitcoin", "NSE:TCS"])
        async for event in sock.events():                                        # quotes and pushed lists
            print(event["type"], event.get("data"))

The connection reconnects by itself (exponential backoff) and subscribes again. Requests that were
waiting when it dropped fail with ConnectionFailed, so retry them if they are safe to repeat.
"""
import asyncio
import itertools
import json
import random
from typing import Any, AsyncIterator, Dict, Optional, Tuple

from .errors import (AuthError, ConnectionFailed, MarketApiError, NotFoundError,
                     RateLimitError, UpstreamError)

_ERRORS = {"unauthorized": AuthError, "rate_limited": RateLimitError, "not_found": NotFoundError,
           "upstream_error": UpstreamError}
_FATAL_AT_CONNECT = {"unauthorized", "rate_limited"}


def _ws_url(base_url: str) -> str:
    return base_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/v1/ws"


def _error_from(frame: dict) -> MarketApiError:
    code = frame.get("code", "error")
    message = frame.get("message", "Request failed")
    if frame.get("hint"):
        message += f" Hint: {frame['hint']}"
    return _ERRORS.get(code, MarketApiError)(message, status=frame.get("status"), code=code,
                                             retry_after=frame.get("retry_after"))


class Socket:
    def __init__(self, base_url: str, api_key: Optional[str] = None, *, reconnect: bool = True,
                 request_timeout: float = 60, max_backoff: float = 30, queue_size: int = 1000):
        self.url = _ws_url(base_url)
        self.api_key = api_key
        self.reconnect = reconnect
        self.request_timeout = request_timeout
        self.max_backoff = max_backoff
        self.hello: Optional[dict] = None                  # what the server offers, after connecting
        self._ids = itertools.count(1)
        self._pending: Dict[str, asyncio.Future] = {}
        self._quote_symbols: set = set()                    # resolved symbols, replayed after a reconnect
        self._channel_subs: Dict[str, dict] = {}            # other channels by key, replayed after a reconnect
        self._events: asyncio.Queue = asyncio.Queue(maxsize=queue_size)
        self._ready = asyncio.Event()
        self._ws = None
        self._task: Optional[asyncio.Task] = None
        self._closed = False
        self._fatal: Optional[Exception] = None

    # ── lifecycle ────────────────────────────────────────────────
    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def connect(self, timeout: float = 20) -> dict:
        if self._task is None:
            self._task = asyncio.create_task(self._run())
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
        except asyncio.TimeoutError:
            await self.close()
            raise ConnectionFailed(f"No answer from {self.url} within {timeout} seconds.")
        if self._fatal:
            raise self._fatal
        return self.hello

    async def close(self) -> None:
        self._closed = True
        if self._ws is not None:
            await self._ws.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._fail_pending(ConnectionFailed("Connection closed."))
        self._offer({"type": "closed"})

    # ── asking and subscribing ───────────────────────────────────
    async def call_full(self, op: str, **params) -> Tuple[Any, dict]:
        """Run one operation; returns (data, meta)."""
        frame = await self._request({"op": op, "params": params})
        return frame.get("data"), frame.get("meta", {})

    async def call(self, op: str, **params) -> Any:
        """Run one operation; returns just the data. Raises a typed MarketApiError on failure."""
        return (await self.call_full(op, **params))[0]

    async def subscribe(self, channel: str = "quotes", **params) -> dict:
        reply = await self._request({"op": "subscribe", "params": {"channel": channel, **params}})
        if channel == "quotes":
            self._quote_symbols.update(reply.get("symbols", []))
        else:
            self._channel_subs[reply.get("key") or json.dumps(params, sort_keys=True)] = {"channel": channel, **params}
        return reply

    async def unsubscribe(self, channel: str = "quotes", **params) -> dict:
        reply = await self._request({"op": "unsubscribe", "params": {"channel": channel, **params}})
        if channel == "quotes":
            self._quote_symbols.difference_update(reply.get("symbols", []))
        else:
            self._channel_subs.pop(reply.get("key"), None)
        return reply

    async def ping(self) -> float:
        loop = asyncio.get_running_loop()
        started = loop.time()
        await self._request({"op": "ping"})
        return loop.time() - started

    # ── receiving live data ──────────────────────────────────────
    async def events(self) -> AsyncIterator[dict]:
        """Every pushed frame: {"type": "quote", "data": ...}, {"type": "update", ...} and status changes
        ({"type": "disconnected"} / {"type": "connected"}). Ends when the socket is closed."""
        while True:
            event = await self._events.get()
            if event.get("type") == "closed":
                return
            yield event

    async def quotes(self) -> AsyncIterator[dict]:
        """Only the quote objects, without the wrapper."""
        async for event in self.events():
            if event.get("type") == "quote":
                yield event["data"]

    # ── internals ────────────────────────────────────────────────
    async def _request(self, body: dict) -> dict:
        if self._closed:
            raise ConnectionFailed("The socket is closed.")
        if not self._ready.is_set() or self._ws is None:
            await self.connect()
        frame_id = str(next(self._ids))
        fut = asyncio.get_running_loop().create_future()
        self._pending[frame_id] = fut
        try:
            await self._ws.send(json.dumps({"id": frame_id, **body}))
            return await asyncio.wait_for(fut, self.request_timeout)
        except asyncio.TimeoutError:
            raise MarketApiError(f"No answer to '{body.get('op')}' within {self.request_timeout} seconds.", code="timeout")
        except OSError as e:
            raise ConnectionFailed(f"Connection lost: {e}") from e
        finally:
            self._pending.pop(frame_id, None)

    def _fail_pending(self, error: Exception) -> None:
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(error)

    def _offer(self, event: dict) -> None:
        if self._events.full():          # slow consumer: drop the oldest, keep the newest
            try:
                self._events.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self._events.put_nowait(event)

    async def _run(self) -> None:
        try:
            from websockets.asyncio.client import connect
            from websockets.exceptions import ConnectionClosed
        except ImportError as e:  # pragma: no cover
            self._fatal = ImportError("The socket needs the 'websockets' package: pip install tickvale[live]")
            self._ready.set()
            raise self._fatal from e

        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        backoff, first = 1.0, True
        while not self._closed:
            try:
                async with connect(self.url, additional_headers=headers, open_timeout=15, max_size=2 ** 22) as ws:
                    self._ws = ws
                    backoff = 1.0
                    async for raw in ws:
                        await self._on_frame(json.loads(raw), first)
            except (ConnectionClosed, OSError, asyncio.TimeoutError):
                pass
            except asyncio.CancelledError:
                raise
            finally:
                self._ws = None
                self._fail_pending(ConnectionFailed("Connection lost."))
            if self._fatal or self._closed or not self.reconnect:
                break
            self._offer({"type": "disconnected"})
            first = False
            self._ready.clear()
            await asyncio.sleep(backoff + random.random() * 0.5)
            backoff = min(backoff * 2, self.max_backoff)
        self._ready.set()

    async def _on_frame(self, frame: dict, first_connection: bool) -> None:
        kind = frame.get("type")
        if kind == "hello":
            self.hello = frame
            self._ready.set()
            replay = [{"channel": "quotes", "symbols": sorted(self._quote_symbols)}] if self._quote_symbols else []
            replay += list(self._channel_subs.values())  # after a reconnect: pick up where we left off
            for params in replay:
                await self._ws.send(json.dumps({"id": f"resub{next(self._ids)}", "op": "subscribe", "params": params}))
            if not first_connection:
                self._offer({"type": "connected"})
        elif kind in ("quote", "update"):
            self._offer(frame)
        elif kind == "error" and frame.get("id") in self._pending:
            self._pending[frame["id"]].set_exception(_error_from(frame))
        elif kind == "error" and frame.get("id") is None and frame.get("code") in _FATAL_AT_CONNECT and not self._ready.is_set():
            self._fatal = _error_from(frame)              # rejected while connecting: do not retry
            self._ready.set()
        elif kind in ("result", "subscribed", "unsubscribed", "pong") and frame.get("id") in self._pending:
            fut = self._pending[frame["id"]]
            if not fut.done():
                fut.set_result(frame)
        elif kind == "error":
            self._offer(frame)                            # unsolicited problem (for example a pushed list failed)
