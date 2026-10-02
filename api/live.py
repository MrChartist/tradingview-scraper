"""Live quotes: one shared upstream TradingView connection fanned out to many clients.

Why a hub: opening one upstream socket per client would not scale and would get
the server blocked. Instead the hub keeps a single quote session, reference-counts
symbols (add upstream when the first client wants one, remove when the last leaves),
merges TradingView's partial updates into full quotes, and hands each client only the
latest value per symbol (slow clients never build up a backlog).
"""
import asyncio
import json
import logging
import random
import re
import string
import time
from collections import Counter
from typing import Dict, Iterable, List, Optional, Set

from websockets.asyncio.client import connect

logger = logging.getLogger("market_terminal.live")

UPSTREAM_URL = "wss://data.tradingview.com/socket.io/websocket?from=screener%2F"
FIELDS = [
    "lp", "ch", "chp", "volume", "lp_time", "currency_code", "short_name", "description",
    "exchange", "type", "update_mode", "current_session", "open_price", "high_price",
    "low_price", "prev_close_price", "bid", "ask", "rtc", "rchp",
]
SYMBOL_RE = re.compile(r"^[A-Z0-9_.!&\-]{1,30}:[A-Z0-9_.!&\-/]{1,40}$")
_FRAME_SPLIT = re.compile(r"~m~\d+~m~")
IDLE_SECONDS = 60


def valid_symbol(symbol: str) -> bool:
    return bool(SYMBOL_RE.match(symbol))


def frame(payload: str) -> str:
    return f"~m~{len(payload)}~m~{payload}"


def message(name: str, params: list) -> str:
    return frame(json.dumps({"m": name, "p": params}, separators=(",", ":")))


def delay_info(update_mode: Optional[str]) -> dict:
    """update_mode looks like 'streaming', 'delayed_streaming_900' or 'endofday'."""
    mode = update_mode or ""
    m = re.search(r"delayed_streaming_(\d+)", mode)
    return {
        "realtime": mode == "streaming",
        "delayed": mode.startswith("delayed"),
        "delay_seconds": int(m.group(1)) if m else None,
    }


def normalize(symbol: str, state: dict) -> dict:
    """Turn TradingView's raw quote fields into the stable public shape."""
    if state.get("_error"):
        return {"symbol": symbol, "status": "error", "error": "symbol_not_found"}
    return {
        "symbol": symbol,
        "status": "ok",
        "price": state.get("lp"),
        "change": state.get("ch"),
        "change_percent": state.get("chp"),
        "open": state.get("open_price"),
        "high": state.get("high_price"),
        "low": state.get("low_price"),
        "prev_close": state.get("prev_close_price"),
        "volume": state.get("volume"),
        "bid": state.get("bid"),
        "ask": state.get("ask"),
        "currency": state.get("currency_code"),
        "name": state.get("short_name"),
        "description": state.get("description"),
        "exchange": state.get("exchange"),
        "type": state.get("type"),
        "session": state.get("current_session"),
        "update_mode": state.get("update_mode"),
        **delay_info(state.get("update_mode")),
        "time": state.get("lp_time"),
    }


class Subscription:
    """One client's view of the hub. Holds only the newest quote per symbol."""

    def __init__(self, client: str):
        self.client = client
        self.symbols: Set[str] = set()
        self._pending: Dict[str, dict] = {}
        self._event = asyncio.Event()
        self.closed = False

    def push(self, quote: dict) -> None:
        self._pending[quote["symbol"]] = quote
        self._event.set()

    async def next_batch(self, timeout: Optional[float] = None) -> List[dict]:
        """Wait for updates; returns [] on timeout (use it to send keep-alives)."""
        try:
            await asyncio.wait_for(self._event.wait(), timeout)
        except asyncio.TimeoutError:
            return []
        self._event.clear()
        batch, self._pending = list(self._pending.values()), {}
        return batch


class QuoteHub:
    def __init__(self, max_symbols: int = 500):
        self.max_symbols = max_symbols
        self._refs: Counter = Counter()
        self._subs_by_symbol: Dict[str, Set[Subscription]] = {}
        self._state: Dict[str, dict] = {}
        self._task: Optional[asyncio.Task] = None
        self._ws = None
        self._session = "qs_" + "".join(random.choices(string.ascii_lowercase, k=12))
        self._idle_since: Optional[float] = None
        self.connected = False
        self.last_message_at: Optional[float] = None
        self.reconnects = 0

    # ── client-facing API ────────────────────────────────────────
    async def subscribe(self, sub: Subscription, symbols: Iterable[str]) -> List[str]:
        """Add symbols for this client. Returns the ones newly accepted."""
        accepted, new_upstream = [], []
        for sym in symbols:
            if sym in sub.symbols:
                continue
            if self._refs[sym] == 0 and len(self._refs) >= self.max_symbols:
                raise OverflowError("The server is tracking its maximum number of symbols right now. Try again later.")
            sub.symbols.add(sym)
            self._refs[sym] += 1
            self._subs_by_symbol.setdefault(sym, set()).add(sub)
            accepted.append(sym)
            if self._refs[sym] == 1:
                new_upstream.append(sym)
            elif sym in self._state:      # already live: send the current snapshot at once
                sub.push(normalize(sym, self._state[sym]))
        self._idle_since = None
        self._ensure_running()
        if new_upstream and self._ws is not None:
            await self._send("quote_add_symbols", [self._session, *new_upstream])
        return accepted

    async def unsubscribe(self, sub: Subscription, symbols: Iterable[str]) -> None:
        gone = []
        for sym in list(symbols):
            if sym not in sub.symbols:
                continue
            sub.symbols.discard(sym)
            self._subs_by_symbol.get(sym, set()).discard(sub)
            self._refs[sym] -= 1
            if self._refs[sym] <= 0:
                del self._refs[sym]
                self._subs_by_symbol.pop(sym, None)
                self._state.pop(sym, None)
                gone.append(sym)
        if gone and self._ws is not None:
            await self._send("quote_remove_symbols", [self._session, *gone])
        if not self._refs:
            self._idle_since = time.time()

    async def release(self, sub: Subscription) -> None:
        sub.closed = True
        await self.unsubscribe(sub, list(sub.symbols))

    def stats(self) -> dict:
        return {
            "connected": self.connected,
            "symbols": len(self._refs),
            "subscribers": len({s for subs in self._subs_by_symbol.values() for s in subs}),
            "reconnects": self.reconnects,
            "last_message_age_seconds": None if self.last_message_at is None else round(time.time() - self.last_message_at, 1),
        }

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    # ── upstream connection ──────────────────────────────────────
    def _ensure_running(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="quote-hub")

    async def _send(self, name: str, params: list) -> None:
        ws = self._ws
        if ws is None:
            return
        try:
            await ws.send(message(name, params))
        except Exception as e:  # the read loop will notice the dead socket and reconnect
            logger.warning("Upstream send failed: %s", e)

    async def _run(self) -> None:
        backoff = 1
        while True:
            try:
                async with connect(
                    UPSTREAM_URL,
                    origin="https://www.tradingview.com",
                    additional_headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"},
                    open_timeout=15, ping_interval=None, max_size=2 ** 22,
                ) as ws:
                    self._ws = ws
                    self.connected = True
                    backoff = 1
                    await self._handshake()
                    logger.info("Upstream connected, %d symbols", len(self._refs))
                    await self._read_loop(ws)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Upstream connection lost: %s", e)
            finally:
                self._ws = None
                self.connected = False
            if self._should_stop():
                return
            self.reconnects += 1
            await asyncio.sleep(backoff + random.random())
            backoff = min(backoff * 2, 30)

    def _should_stop(self) -> bool:
        return not self._refs and self._idle_since is not None and time.time() - self._idle_since >= IDLE_SECONDS

    async def _handshake(self) -> None:
        await self._send("set_auth_token", ["unauthorized_user_token"])
        await self._send("set_locale", ["en", "US"])
        self._session = "qs_" + "".join(random.choices(string.ascii_lowercase, k=12))
        await self._send("quote_create_session", [self._session])
        await self._send("quote_set_fields", [self._session, *FIELDS])
        if self._refs:   # re-subscribe after a reconnect
            await self._send("quote_add_symbols", [self._session, *self._refs.keys()])

    async def _read_loop(self, ws) -> None:
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=20)
            except asyncio.TimeoutError:
                if self._should_stop():
                    return
                continue
            self.last_message_at = time.time()
            for payload in (p for p in _FRAME_SPLIT.split(raw) if p):
                if payload.startswith("~h~"):          # heartbeat: echo it back
                    await ws.send(frame(payload))
                    continue
                try:
                    data = json.loads(payload)
                except ValueError:
                    continue
                if isinstance(data, dict) and data.get("m") == "qsd":
                    self._on_quote(data["p"][1])
            if self._should_stop():
                return

    def _on_quote(self, item: dict) -> None:
        symbol = item.get("n")
        if not symbol or symbol not in self._refs:
            return
        state = self._state.setdefault(symbol, {})
        if item.get("s") != "ok":
            state["_error"] = True
        else:
            state.pop("_error", None)
            state.update(item.get("v") or {})
        if not state.get("_error") and state.get("lp") is None:
            return       # nothing usable yet (first packets often carry only metadata)
        quote = normalize(symbol, state)
        for sub in list(self._subs_by_symbol.get(symbol, ())):
            sub.push(quote)
