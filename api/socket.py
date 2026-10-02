"""The Tickvale WebSocket: one connection for everything.

    wss://host/v1/ws        (key as X-API-Key header, Authorization: Bearer, or ?api_key=)

Client -> server (JSON text frames)
    {"id": "1", "op": "quotes", "params": {"symbols": ["reliance", "bitcoin"]}}      ask, get one answer
    {"op": "subscribe",   "params": {"channel": "quotes", "symbols": ["NSE:TCS"]}}   start a live feed
    {"op": "unsubscribe", "params": {"channel": "quotes", "symbols": ["NSE:TCS"]}}
    {"op": "ping"}
  Older frames {"action": "subscribe", "symbols": [...]} still work.

Server -> client
    hello         once, right after connecting: what is available and the limits
    result        answer to a request   {"type": "result", "id", "op", "data", "meta"}
    error         {"type": "error", "id", "code", "message", "hint"}   (the connection stays open)
    subscribed    / unsubscribed
    quote         a live quote           {"type": "quote", "data": {...}}
    update        a pushed list          {"type": "update", "channel", "key", "data", "meta"}
    heartbeat     every 20 seconds
    pong

Capabilities come from api/operations.py (requests) and the CHANNELS registry below (live feeds).
See docs/WEBSOCKET.md and docs/EXTENDING.md.
"""
import asyncio
import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from api import operations as ops
from api.config import Settings
from api.errors import ApiError
from api.live import QuoteHub, Subscription, valid_symbol
from api.operations import Context
from api.providers import registry
from api.security import Guard

logger = logging.getLogger("market_terminal.socket")

MAX_MESSAGE_BYTES = 64 * 1024
MAX_INFLIGHT = 8              # requests running at once per connection
OP_TIMEOUT_SECONDS = 45
HEARTBEAT_SECONDS = 20
MAX_PROTOCOL_ERRORS = 10      # malformed frames tolerated before we hang up


def error_frame(frame_id, code: str, message: str, hint: Optional[str] = None, **extra) -> dict:
    out = {"type": "error", "id": frame_id, "code": code, "message": message}
    if hint:
        out["hint"] = hint
    out.update(extra)
    return out


def api_error_frame(frame_id, e: ApiError) -> dict:
    return error_frame(frame_id, e.code, e.message, e.hint, status=e.status)


# ═══════════════════════════════════════════════════════════════════
#  Channels: live feeds a client can subscribe to
# ═══════════════════════════════════════════════════════════════════
class Channel:
    """Base class. Add a subclass and decorate it with @channel to publish a new live feed."""
    name = ""
    summary = ""
    params = ""

    async def subscribe(self, session: "Session", params: dict) -> dict:
        raise NotImplementedError

    async def unsubscribe(self, session: "Session", params: dict) -> dict:
        raise NotImplementedError

    async def close(self, session: "Session") -> None:
        pass


CHANNELS: Dict[str, Channel] = {}


def channel(cls):
    inst = cls()
    if not inst.name or inst.name in CHANNELS:
        raise ValueError(f"Bad or duplicate channel name '{inst.name}'")
    CHANNELS[inst.name] = inst
    return cls


@channel
class QuotesChannel(Channel):
    name = "quotes"
    summary = "Live prices. Symbols can be codes (NSE:TCS) or plain names (bitcoin)."
    params = "symbols: list of symbols or names"

    async def _targets(self, session: "Session", raw) -> tuple:
        """Resolve names to symbols one by one so one bad item doesn't spoil the rest."""
        good: List[str] = []
        rejected: List[dict] = []
        resolved: Dict[str, str] = {}
        items = ops.as_list(raw)

        async def look_up(item):
            try:
                return await run_in_threadpool(ops.resolve_name, item, session.ctx)
            except ApiError as e:
                return e

        for item, full in zip(items, await asyncio.gather(*[look_up(i) for i in items])):   # side by side
            if isinstance(full, ApiError):
                rejected.append({"symbol": item.upper(), "reason": "not_found" if full.status == 404 else "unavailable"})
                continue
            if not valid_symbol(full):
                rejected.append({"symbol": item.upper(), "reason": "invalid_format"})
            elif full not in good:
                good.append(full)
                if full != item.upper():
                    resolved[item] = full
        return good, rejected, resolved

    async def subscribe(self, session, params):
        raw = params.get("symbols")
        if not ops.as_list(raw):
            raise ApiError(400, "Say which symbols you want, for example {\"symbols\": [\"NSE:TCS\", \"bitcoin\"]}.",
                           hint="Names like 'reliance' work too.")
        good, rejected, resolved = await self._targets(session, raw)
        sub = session.quote_sub
        room = max(session.max_symbols() - len(sub.symbols), 0)
        for sym in good[room:]:
            rejected.append({"symbol": sym, "reason": "too_many_symbols"})
        try:
            accepted = await session.hub.subscribe(sub, good[:room])
        except OverflowError as e:
            raise ApiError(503, str(e))
        if session.quote_pump is None or session.quote_pump.done():
            session.quote_pump = asyncio.create_task(self._pump(session))
        return {"channel": "quotes", "symbols": accepted, "rejected": rejected, "resolved": resolved}

    async def unsubscribe(self, session, params):
        good, _, _ = await self._targets(session, params.get("symbols"))
        await session.hub.unsubscribe(session.quote_sub, good)
        return {"channel": "quotes", "symbols": good}

    async def _pump(self, session):
        sub = session.quote_sub
        while not sub.closed:
            for quote in await sub.next_batch():
                await session.send({"type": "quote", "data": quote})

    async def close(self, session):
        if session.quote_pump:
            session.quote_pump.cancel()
        await session.hub.release(session.quote_sub)


@channel
class MoversChannel(Channel):
    name = "movers"
    summary = "A market's gainers, losers or most active, pushed whenever the list changes."
    params = "market, category, limit, interval (15-300 seconds)"
    MAX_PER_SESSION = 3

    async def subscribe(self, session, params):
        interval = max(15, min(int(params.get("interval", 30) or 30), 300))
        query = {k: params[k] for k in ("market", "category", "limit") if k in params}
        first = await run_in_threadpool(ops.execute, "movers", query, session.ctx)   # fail fast on bad input
        key = f"{first.meta['market']}:{first.meta['category']}"
        tasks = session.state.setdefault("movers", {})
        if key not in tasks and len(tasks) >= self.MAX_PER_SESSION:
            raise ApiError(429, f"At most {self.MAX_PER_SESSION} movers lists per connection.", hint="Unsubscribe from one first.")
        if key in tasks:
            tasks[key].cancel()
        tasks[key] = asyncio.create_task(self._push(session, key, query, first, interval))
        return {"channel": "movers", "key": key, "interval": interval}

    async def unsubscribe(self, session, params):
        tasks = session.state.get("movers", {})
        key = f"{params.get('market', 'stocks-india')}:{params.get('category', 'gainers')}"
        task = tasks.pop(key, None)
        if task:
            task.cancel()
        return {"channel": "movers", "key": key}

    async def _push(self, session, key, query, result, interval):
        last = None
        while True:
            digest = hashlib.md5(json.dumps(result.data, sort_keys=True, default=str).encode()).hexdigest()
            if digest != last:         # only speak when something changed
                last = digest
                await session.send({"type": "update", "channel": "movers", "key": key, "data": result.data, "meta": result.meta})
            await asyncio.sleep(interval)
            try:
                result = await run_in_threadpool(ops.execute, "movers", query, session.ctx)
            except ApiError as e:
                await session.send(api_error_frame(None, e))
                await asyncio.sleep(interval)

    async def close(self, session):
        for task in session.state.get("movers", {}).values():
            task.cancel()


# ═══════════════════════════════════════════════════════════════════
#  One connection
# ═══════════════════════════════════════════════════════════════════
class Session:
    def __init__(self, ws: WebSocket, guard: Guard, settings: Settings, version: str):
        self.ws = ws
        self.guard = guard
        self.settings = settings
        self.version = version
        self.hub: QuoteHub = ws.app.state.hub
        self.ctx = Context(settings=settings, hub=self.hub, auto_resolve=True)
        self.policy = guard.policy(None)
        self.who = "?"
        self.state: Dict[str, Any] = {}
        self.quote_sub: Subscription = Subscription("?")
        self.quote_pump: Optional[asyncio.Task] = None
        self._tasks: set = set()
        self._gate = asyncio.Semaphore(MAX_INFLIGHT)
        self._send_lock = asyncio.Lock()
        self._bad_frames = 0

    async def send(self, frame: dict) -> None:
        async with self._send_lock:
            await self.ws.send_text(json.dumps(frame, default=str, separators=(",", ":")))

    async def _refuse(self, code: int, e: ApiError) -> None:
        await self.send(api_error_frame(None, e))
        await self.ws.close(code=code)

    def max_symbols(self) -> int:
        return min(self.settings.ws_max_symbols_per_client, self.policy.max_symbols or 10 ** 6)

    def hello(self) -> dict:
        s = self.settings
        return {
            "type": "hello", "service": "tickvale", "version": self.version, "server_time": int(time.time()),
            "auth": "api_key" if s.auth_enabled else "open", "client": self.who,
            "operations": ops.describe(self.policy),
            "sources": [{"name": p.name, "capabilities": sorted(p.capabilities)} for p in registry.enabled],
            "channels": [{"name": c.name, "summary": c.summary, "params": c.params}
                         for c in CHANNELS.values() if self.policy.allows_channel(c.name)],
            "limits": {"max_symbols": self.max_symbols(), "max_in_flight": MAX_INFLIGHT,
                       "max_message_bytes": MAX_MESSAGE_BYTES, "requests_per_minute": self.guard.limit_for(self.who)},
            "how_to": {"ask": {"id": "1", "op": "quotes", "params": {"symbols": ["reliance", "bitcoin"]}},
                       "subscribe": {"op": "subscribe", "params": {"channel": "quotes", "symbols": ["NSE:TCS"]}}},
            "note": "Free stock prices are usually 15 minutes delayed. Check each quote's 'freshness'.",
        }

    async def run(self, slots) -> None:
        await self.ws.accept()
        try:
            self.who = self.guard.websocket(self.ws)
        except ApiError as e:
            return await self._refuse(4401, e)
        self.quote_sub = Subscription(self.who)
        self.policy = self.guard.policy(self.who)
        self.ctx.policy = self.policy
        allowed, _, reset = self.guard.limiter.hit(self.who, self.guard.limit_for(self.who))
        if not allowed:
            return await self._refuse(4429, ApiError(429, "Rate limit exceeded.", hint=f"Wait {reset} seconds."))
        try:
            slots.acquire(self.who)
        except ApiError as e:
            return await self._refuse(4429, e)

        heartbeat = asyncio.create_task(self._heartbeat())
        try:
            await self.send(self.hello())
            while True:
                text = await self.ws.receive_text()
                if len(text) > MAX_MESSAGE_BYTES:
                    await self.send(error_frame(None, "message_too_large", f"Messages can be at most {MAX_MESSAGE_BYTES} bytes."))
                    return await self.ws.close(code=1009)
                frame = self._parse(text)
                if frame is None:
                    self._bad_frames += 1
                    await self.send(error_frame(None, "bad_message", "Send JSON like {\"op\": \"quotes\", \"params\": {...}}.",
                                                "See the 'how_to' field in the hello message."))
                    if self._bad_frames >= MAX_PROTOCOL_ERRORS:
                        return await self.ws.close(code=4400)
                    continue
                await self._dispatch(frame)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            heartbeat.cancel()
            for t in list(self._tasks):
                t.cancel()
            for ch in CHANNELS.values():
                try:
                    await ch.close(self)
                except Exception:       # cleanup must never raise
                    logger.exception("Channel %s failed to close", ch.name)
            slots.release(self.who)

    @staticmethod
    def _parse(text: str) -> Optional[dict]:
        try:
            frame = json.loads(text)
        except ValueError:
            return None
        if not isinstance(frame, dict):
            return None
        if "action" in frame and "op" not in frame:        # older frames: {"action": "subscribe", "symbols": [...]}
            action = frame["action"]
            if action in ("subscribe", "unsubscribe"):
                return {"op": action, "id": frame.get("id"), "params": {"channel": "quotes", "symbols": frame.get("symbols", [])}}
            return {"op": action, "id": frame.get("id")}
        return frame

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            await self.send({"type": "heartbeat", "time": int(time.time())})

    def _spend(self) -> Optional[dict]:
        allowed, _, reset = self.guard.limiter.hit(self.who, self.guard.limit_for(self.who))
        return None if allowed else error_frame(None, "rate_limited", "Rate limit exceeded.",
                                                f"Wait {reset} seconds. Subscribe to a channel instead of asking repeatedly.", retry_after=reset)

    async def _dispatch(self, frame: dict) -> None:
        fid, op = frame.get("id"), frame.get("op")
        params = frame.get("params") or {}
        if not isinstance(op, str) or not isinstance(params, dict):
            return await self.send(error_frame(fid, "bad_message", "Every message needs an 'op' (text) and optional 'params' (an object).",
                                               "Example: {\"op\": \"quotes\", \"params\": {\"symbols\": [\"reliance\"]}}"))
        if op == "ping":
            return await self.send({"type": "pong", "id": fid, "time": int(time.time())})
        limited = self._spend()
        if limited:
            limited["id"] = fid
            return await self.send(limited)
        if op in ("subscribe", "unsubscribe"):
            return await self._channel_op(fid, op, params)
        task = asyncio.create_task(self._request(fid, op, params))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _channel_op(self, fid, op: str, params: dict) -> None:
        name = params.get("channel", "quotes")
        ch = CHANNELS.get(name)
        if ch is None:
            return await self.send(error_frame(fid, "unknown_channel", f"No channel called '{name}'.",
                                               "Available: " + ", ".join(CHANNELS) + "."))
        if not self.policy.allows_channel(name):
            return await self.send(error_frame(fid, "forbidden", f"The key for '{self.who}' is not allowed to use the '{name}' feed.",
                                               "Ask the owner of the server to add it to this client's channels."))
        try:
            info = await (ch.subscribe if op == "subscribe" else ch.unsubscribe)(self, params)
        except ApiError as e:
            return await self.send(api_error_frame(fid, e))
        except Exception:
            logger.exception("Channel %s failed", name)
            return await self.send(error_frame(fid, "internal_error", "Something went wrong on our side.", "Try again in a moment."))
        await self.send({"type": "subscribed" if op == "subscribe" else "unsubscribed", "id": fid, **info})

    async def _request(self, fid, op: str, params: dict) -> None:
        async with self._gate:
            try:
                result = await asyncio.wait_for(run_in_threadpool(ops.execute, op, params, self.ctx), OP_TIMEOUT_SECONDS)
                await self.send({"type": "result", "id": fid, "op": op, "data": result.data, "meta": result.meta})
            except ApiError as e:
                await self.send(api_error_frame(fid, e))
            except asyncio.TimeoutError:
                await self.send(error_frame(fid, "timeout", f"'{op}' took longer than {OP_TIMEOUT_SECONDS} seconds.", "Try again, or ask for less data."))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Operation %s failed", op)
                await self.send(error_frame(fid, "internal_error", "Something went wrong on our side.", "Try again in a moment."))


def register_socket(router: APIRouter, guard: Guard, settings: Settings, version: str) -> None:
    @router.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        """The primary interface. Open it, read the `hello` message, then ask or subscribe. See docs/WEBSOCKET.md."""
        await Session(ws, guard, settings, version).run(ws.app.state.slots)
