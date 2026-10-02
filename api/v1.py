"""Product API, version 1: /v1/*

Every JSON response is {"data": ..., "meta": {...}}; every error is
{"error": {"code", "message", "request_id"}}. See docs/API.md.
"""
import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any, List, Literal, Optional

from fastapi import HTTPException, APIRouter, Depends, Query, Request, Security, WebSocket
from fastapi.security import APIKeyHeader
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api import services as svc
from api.config import Settings
from api.errors import ApiError
from api.live import QuoteHub, Subscription, valid_symbol
from api.security import Guard

Market = Query("stocks-india", description="stocks-india, stocks-usa, stocks-uk, stocks-canada, stocks-australia, crypto, forex")


def envelope(request: Request, data: Any, **meta) -> dict:
    return {"data": data, "meta": {"request_id": getattr(request.state, "request_id", None), **meta}}


def parse_symbols(raw: str, limit: int) -> List[str]:
    symbols = list(dict.fromkeys(s.strip().upper() for s in raw.split(",") if s.strip()))
    if not symbols:
        raise ApiError(400, "Pass at least one symbol, for example symbols=NSE:RELIANCE,NASDAQ:AAPL.")
    if len(symbols) > limit:
        raise ApiError(400, f"At most {limit} symbols per request.")
    bad = [s for s in symbols if not valid_symbol(s)]
    if bad:
        raise ApiError(400, f"Invalid symbol format: {', '.join(bad[:5])}. Use EXCHANGE:TICKER, for example NSE:RELIANCE.")
    return symbols


def parse_when(value: Optional[str], default: int) -> int:
    """Accept epoch seconds or an ISO date (YYYY-MM-DD)."""
    if not value:
        return default
    try:
        if value.isdigit():
            return int(value)
        return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        raise ApiError(400, f"Invalid date '{value}'. Use YYYY-MM-DD or epoch seconds.")


# ── Screener request model ────────────────────────────────────────
OPERATORS = {"gt": "greater", "gte": "egreater", "lt": "less", "lte": "eless",
             "eq": "equal", "neq": "nequal", "in": "in_range", "between": "in_range"}
FIELD_PATTERN = r"^[A-Za-z0-9_.]{1,60}$"


class Condition(BaseModel):
    field: str = Field(..., pattern=FIELD_PATTERN, examples=["market_cap_basic"])
    op: Literal["gt", "gte", "lt", "lte", "eq", "neq", "in", "between"]
    value: Any = Field(..., description="A number or string; a list for 'in' and [low, high] for 'between'.")


class ScreenerRequest(BaseModel):
    market: str = Field("india", examples=["india", "america", "crypto"])
    conditions: List[Condition] = Field(default_factory=list, max_length=20)
    columns: Optional[List[str]] = Field(None, max_length=30, description="Fields to return. Defaults to a standard set.")
    sort_by: str = Field("volume", pattern=FIELD_PATTERN)
    sort_order: Literal["asc", "desc"] = "desc"
    limit: int = Field(25, ge=1, le=200)
    main_only: bool = Field(True, description="Restrict to the market's main exchange (for example NSE for India).")


class StreamSlots:
    """Caps concurrent live connections per client."""

    def __init__(self, per_client: int):
        self.per_client = per_client
        self._open: dict = {}

    def acquire(self, client: str) -> None:
        if self._open.get(client, 0) >= self.per_client:
            raise ApiError(429, f"Too many live connections for this key (max {self.per_client}).")
        self._open[client] = self._open.get(client, 0) + 1

    def release(self, client: str) -> None:
        self._open[client] = max(0, self._open.get(client, 1) - 1)


def build_router(guard: Guard, settings: Settings, version: str) -> APIRouter:
    router = APIRouter(prefix="/v1")
    key_header = APIKeyHeader(name="X-API-Key", auto_error=False, description="Your API key (also accepted as 'Authorization: Bearer <key>').")
    auth = [Security(key_header), Depends(guard.http)]
    slots = StreamSlots(settings.ws_max_clients_per_key)

    # ── meta ──────────────────────────────────────────────────────
    @router.get("/health", tags=["meta"], summary="Liveness probe (no key needed)")
    def health():
        return {"status": "ok", "version": version}

    @router.get("/status", tags=["meta"], dependencies=auth, summary="Service status")
    def status(request: Request):
        hub: QuoteHub = request.app.state.hub
        return envelope(request, {
            "version": version, "live": hub.stats(),
            "auth": "api_key" if settings.auth_enabled else "open",
            "rate_limit_per_minute": settings.rate_limit_per_minute,
            "cache_entries": len(svc._CACHE),
        })

    # ── symbols ───────────────────────────────────────────────────
    @router.get("/symbols/search", tags=["symbols"], dependencies=auth, summary="Find a symbol by name or ticker")
    def search(request: Request, q: str = Query(..., min_length=1, max_length=40), limit: int = Query(10, ge=1, le=30)):
        try:
            results = svc.search_symbols_raw(q)[:limit]
        except Exception:
            raise ApiError(502, "Symbol search is unavailable right now.")
        return envelope(request, results, count=len(results))

    @router.get("/quotes", tags=["quotes"], dependencies=auth,
                summary="Latest quote for up to 100 symbols (snapshot)",
                description="Includes `realtime`, `delayed` and `delay_seconds` so you always know how fresh a price is. "
                            "For a continuous feed use `/v1/ws` or `/v1/stream/quotes`.")
    def quotes(request: Request, symbols: str = Query(..., description="Comma separated, e.g. NSE:RELIANCE,NASDAQ:AAPL")):
        wanted = parse_symbols(symbols, 100)
        try:
            found, missing = svc.snapshot_quotes(wanted)
        except Exception as e:
            svc.logger.warning("Snapshot failed: %s", e)
            raise ApiError(502, "Upstream market data is unavailable right now.")
        return envelope(request, found, count=len(found), not_found=missing)

    @router.get("/symbols/{exchange}/{ticker}", tags=["symbols"], dependencies=auth,
                summary="Symbol profile and key statistics (monetary values in the listing currency)")
    def overview(request: Request, exchange: str, ticker: str):
        r = svc.run_scraper(lambda: svc.fetch_overview(exchange, ticker), "Symbol not found or data unavailable.")
        return envelope(request, r["data"], currency=r["data"].get("currency"))

    @router.get("/symbols/{exchange}/{ticker}/fundamentals", tags=["symbols"], dependencies=auth, summary="Fundamentals")
    def fundamentals(request: Request, exchange: str, ticker: str):
        r = svc.run_scraper(lambda: svc.fetch_fundamentals(exchange, ticker), "Fundamental data not found.")
        return envelope(request, r["data"], currency=r["data"].get("currency"))

    @router.get("/symbols/{exchange}/{ticker}/technicals", tags=["symbols"], dependencies=auth, summary="Technical indicator values")
    def technicals(request: Request, exchange: str, ticker: str, timeframe: str = "1d"):
        r = svc.run_scraper(lambda: svc.fetch_indicators(exchange, ticker, timeframe), "Indicators not found.")
        return envelope(request, r["data"], timeframe=timeframe)

    @router.get("/symbols/{exchange}/{ticker}/candles", tags=["symbols"], dependencies=auth,
                summary="Historical OHLCV candles",
                description="Oldest first. `time` is epoch seconds (UTC). Takes a few seconds: it opens a live session upstream.")
    def candles(request: Request, exchange: str, ticker: str, timeframe: str = "1d",
                limit: int = Query(100, ge=1, le=5000)):
        try:
            raw = svc.fetch_ohlcv(exchange, ticker, timeframe, max(limit, 5))
        except (ApiError, HTTPException):
            raise
        except Exception as e:
            svc.logger.warning("Candles failed: %s", e)
            raise ApiError(502, "Could not fetch candles from upstream.")
        if not raw:
            raise ApiError(404, "No candles returned. Check the exchange, ticker and timeframe.")
        out = [{"time": int(c["timestamp"]),
                "datetime": datetime.fromtimestamp(c["timestamp"], timezone.utc).isoformat(),
                "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"],
                "volume": c.get("volume")} for c in raw[-limit:]]
        return envelope(request, out, count=len(out), timeframe=timeframe)

    @router.get("/symbols/{exchange}/{ticker}/news", tags=["symbols"], dependencies=auth, summary="Latest headlines")
    def news(request: Request, exchange: str, ticker: str, limit: int = Query(20, ge=1, le=100), language: str = "en"):
        try:
            items = svc.fetch_news(exchange, ticker, limit, language)
        except Exception as e:
            svc.logger.warning("News failed: %s", e)
            raise ApiError(502, "News is unavailable right now.")
        return envelope(request, items, count=len(items))

    # ── markets ───────────────────────────────────────────────────
    @router.get("/markets/movers", tags=["markets"], dependencies=auth,
                summary="Gainers, losers, most active (liquid, main-exchange listings only)")
    def movers(request: Request, market: str = Market, category: str = "gainers", limit: int = Query(25, ge=1, le=100)):
        r = svc.run_scraper(
            lambda: svc.cached(("movers", market, category, limit),
                               lambda: svc.movers_scraper.scrape(market=market, category=category, limit=limit)),
            "No data.")
        return envelope(request, r["data"], count=len(r["data"]), market=market, category=category)

    @router.post("/screener", tags=["markets"], dependencies=auth, summary="Screen a market with your own conditions")
    def screener(request: Request, body: ScreenerRequest):
        filters = [{"left": c.field, "operation": OPERATORS[c.op], "right": c.value} for c in body.conditions]
        if body.main_only and body.market in svc.MAIN_EXCHANGES:
            filters.append({"left": "exchange", "operation": "in_range", "right": svc.MAIN_EXCHANGES[body.market]})
        columns = body.columns or (svc.STOCK_SCREENER_COLUMNS if body.market in svc.STOCK_SCREENER_MARKETS else None)
        key = ("screener-v1", body.market, tuple(columns or ()), body.sort_by, body.sort_order, body.limit,
               json.dumps(filters, sort_keys=True, default=str))
        r = svc.run_scraper(
            lambda: svc.cached(key, lambda: svc.screener_scraper.screen(
                market=body.market, filters=filters or None, columns=columns,
                sort_by=body.sort_by, sort_order=body.sort_order, limit=body.limit)),
            "No data.")
        return envelope(request, r["data"], count=len(r["data"]), total_matches=r.get("totalCount"), market=body.market)

    # ── calendar ──────────────────────────────────────────────────
    def calendar(kind: str):
        def handler(request: Request, markets: str = Query("india", description="Comma separated: india, america, uk, ..."),
                    date_from: Optional[str] = Query(None, alias="from", description="YYYY-MM-DD or epoch seconds. Default: today."),
                    date_to: Optional[str] = Query(None, alias="to", description="YYYY-MM-DD or epoch seconds. Default: 7 days ahead."),
                    limit: int = Query(100, ge=1, le=500)):
            now = int(time.time())
            start, end = parse_when(date_from, now - now % 86400), parse_when(date_to, now - now % 86400 + 7 * 86400)
            if end < start:
                raise ApiError(400, "'to' must not be before 'from'.")
            try:
                rows = svc.fetch_calendar(kind, [m.strip() for m in markets.split(",") if m.strip()], start, end, limit)
            except Exception as e:
                svc.logger.warning("Calendar failed: %s", e)
                raise ApiError(502, "Calendar data is unavailable right now.")
            return envelope(request, rows, count=len(rows), note="Monetary values are in fundamental_currency_code (usually USD).")
        return handler

    router.add_api_route("/calendar/earnings", calendar("earnings"), methods=["GET"], tags=["calendar"],
                         dependencies=auth, summary="Upcoming and recent earnings")
    router.add_api_route("/calendar/dividends", calendar("dividends"), methods=["GET"], tags=["calendar"],
                         dependencies=auth, summary="Upcoming and recent dividends")

    # ── live ──────────────────────────────────────────────────────
    @router.get("/stream/quotes", tags=["live"], dependencies=auth,
                summary="Server-sent events: continuous quotes for up to 100 symbols",
                description="Events: `quote` (data = a quote object) and `error`. Comment lines are keep-alives. "
                            "Browsers can use EventSource, but it cannot set headers: pass the key as `?api_key=`.")
    async def stream_quotes(request: Request, symbols: str = Query(...)):
        wanted = parse_symbols(symbols, settings.ws_max_symbols_per_client)
        who = request.state.client
        slots.acquire(who)
        hub: QuoteHub = request.app.state.hub
        sub = Subscription(who)
        try:
            await hub.subscribe(sub, wanted)
        except OverflowError as e:
            slots.release(who)
            raise ApiError(503, str(e))

        async def events():
            try:
                yield "retry: 3000\n\n"
                while not sub.closed:
                    batch = await sub.next_batch(timeout=15)
                    if not batch:
                        yield ": keep-alive\n\n"
                        continue
                    for q in batch:
                        yield f"event: quote\ndata: {json.dumps(q)}\n\n"
            finally:
                await hub.release(sub)
                slots.release(who)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @router.websocket("/ws")
    async def websocket_quotes(ws: WebSocket):
        """Live quotes. Send {"action":"subscribe","symbols":[...]}; receive {"type":"quote","data":{...}}."""
        await ws.accept()
        hub: QuoteHub = ws.app.state.hub

        async def fail(code: int, error: str, message: str):
            await ws.send_json({"type": "error", "code": error, "message": message})
            await ws.close(code=code)

        try:
            who = guard.websocket(ws)
        except ApiError as e:
            return await fail(4401, e.code, e.message)
        allowed, _, _ = guard.limiter.hit(who)
        if not allowed:
            return await fail(4429, "rate_limited", "Rate limit exceeded.")
        try:
            slots.acquire(who)
        except ApiError as e:
            return await fail(4429, e.code, e.message)

        sub = Subscription(who)

        async def sender():
            while True:
                batch = await sub.next_batch(timeout=20)
                if not batch:
                    await ws.send_json({"type": "heartbeat", "time": int(time.time())})
                    continue
                for q in batch:
                    await ws.send_json({"type": "quote", "data": q})

        async def receiver():
            while True:
                msg = await ws.receive_json()
                action = msg.get("action") if isinstance(msg, dict) else None
                if action == "ping":
                    await ws.send_json({"type": "pong"})
                elif action in ("subscribe", "unsubscribe"):
                    syms = [str(s).strip().upper() for s in msg.get("symbols", []) if str(s).strip()]
                    if action == "unsubscribe":
                        await hub.unsubscribe(sub, syms)
                        await ws.send_json({"type": "unsubscribed", "symbols": syms})
                        continue
                    rejected = [{"symbol": s, "reason": "invalid_format"} for s in syms if not valid_symbol(s)]
                    good = [s for s in syms if valid_symbol(s)]
                    room = settings.ws_max_symbols_per_client - len(sub.symbols)
                    for s in good[max(room, 0):]:
                        rejected.append({"symbol": s, "reason": "too_many_symbols"})
                    try:
                        accepted = await hub.subscribe(sub, good[:max(room, 0)])
                    except OverflowError as e:
                        await ws.send_json({"type": "error", "code": "unavailable", "message": str(e)})
                        continue
                    await ws.send_json({"type": "subscribed", "symbols": accepted, "rejected": rejected})
                else:
                    await ws.send_json({"type": "error", "code": "bad_request",
                                        "message": "Send {\"action\": \"subscribe\"|\"unsubscribe\"|\"ping\", \"symbols\": [...]}."})

        tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except Exception:
            pass
        finally:
            for t in tasks:
                t.cancel()
            await hub.release(sub)
            slots.release(who)
            try:
                await ws.close()
            except Exception:
                pass

    return router
