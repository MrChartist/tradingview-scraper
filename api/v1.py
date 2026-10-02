"""Product API, version 1: REST under /v1, plus server-sent events.

The WebSocket (api/socket.py) is the primary interface; these routes are thin wrappers over the
same operations (api/operations.py), so every capability behaves identically on both.

Every JSON response is {"data": ..., "meta": {...}}; every error is
{"error": {"code", "message", "hint", "request_id"}}. See docs/API.md.
"""
import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Request, Security
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyHeader

from api import operations as ops
from api.config import Settings
from api.errors import ApiError
from api.live import QuoteHub, Subscription
from api.operations import Context, ScreenerParams
from api.security import Guard
from api.socket import register_socket

Market = Query("stocks-india", description="stocks-india, stocks-usa, stocks-uk, stocks-canada, stocks-australia, crypto, forex")


def envelope(request: Request, data: Any, **meta) -> dict:
    return {"data": data, "meta": {"request_id": getattr(request.state, "request_id", None), **meta}}


class StreamSlots:
    """Caps concurrent live connections (WebSocket and SSE together) per client."""

    def __init__(self, per_client: int):
        self.per_client = per_client
        self._open: dict = {}

    def acquire(self, client: str) -> None:
        if self._open.get(client, 0) >= self.per_client:
            raise ApiError(429, f"Too many live connections for this key (max {self.per_client}).",
                           hint="Close an unused connection, or reuse one connection for several symbols.")
        self._open[client] = self._open.get(client, 0) + 1

    def release(self, client: str) -> None:
        self._open[client] = max(0, self._open.get(client, 1) - 1)


def build_router(guard: Guard, settings: Settings, version: str) -> APIRouter:
    router = APIRouter(prefix="/v1")
    key_header = APIKeyHeader(name="X-API-Key", auto_error=False, description="Your API key (also accepted as 'Authorization: Bearer <key>').")
    auth = [Security(key_header), Depends(guard.http)]

    def run(request: Request, name: str, params: Optional[dict] = None) -> dict:
        who = getattr(request.state, "client", None)          # set by the key check; absent on public help routes
        ctx = Context(settings=settings, hub=request.app.state.hub, auto_resolve=False,
                      policy=guard.policy(who) if who else None)
        result = ops.execute(name, params, ctx)
        return envelope(request, result.data, **result.meta)

    # ── meta & help ───────────────────────────────────────────────
    @router.get("/health", tags=["meta"], summary="Liveness probe (no key needed)")
    def health():
        return {"status": "ok", "version": version}

    @router.get("/status", tags=["meta"], dependencies=auth, summary="Service status")
    def status(request: Request):
        body = run(request, "status")
        body["data"] = {"version": version, **body["data"]}
        return body

    @router.get("/operations", tags=["meta"], summary="Every operation the WebSocket and REST API offer (no key needed)")
    def operations():
        return {"data": ops.describe()}

    @router.get("/schema", tags=["meta"], summary="JSON Schema for every operation's parameters (no key needed)")
    def schema():
        from api.contract import build_schema
        return {"data": build_schema(version)}

    @router.get("/asyncapi.json", tags=["meta"], summary="AsyncAPI description of the WebSocket, for client generators (no key needed)")
    def asyncapi(request: Request):
        from api.contract import build_asyncapi
        return build_asyncapi(version, str(request.base_url))

    @router.get("/markets", tags=["help"], summary="What you can ask for: markets, categories, timeframes, filter fields (no key needed)")
    def catalogue(request: Request):
        return run(request, "markets")

    @router.get("/glossary", tags=["help"], summary="Plain-language meaning of every market term (no key needed)")
    def glossary(request: Request):
        return run(request, "glossary")

    # ── symbols ───────────────────────────────────────────────────
    @router.get("/symbols/resolve", tags=["symbols"], dependencies=auth,
                summary="Turn a name like 'reliance' or 'apple' into the right EXCHANGE:TICKER",
                description="Returns the best match and a few alternatives. Use `best.full_symbol` in the other endpoints.")
    def resolve(request: Request, q: str = Query(..., min_length=1, max_length=60, description="A company, ticker or coin name"),
                country: Optional[str] = Query(None, min_length=2, max_length=2, description="Two-letter country to prefer on ties, e.g. IN or US. Default comes from DEFAULT_COUNTRY.")):
        return run(request, "resolve", {"q": q, "country": country})

    @router.get("/symbols/search", tags=["symbols"], dependencies=auth, summary="Find a symbol by name or ticker")
    def search(request: Request, q: str = Query(..., min_length=1, max_length=40), limit: int = Query(10, ge=1, le=30)):
        return run(request, "search", {"q": q, "limit": limit})

    @router.get("/quotes", tags=["quotes"], dependencies=auth,
                summary="Latest quote for up to 100 symbols (snapshot)",
                description="Includes `freshness`, `realtime`, `delayed` and `delay_seconds` so you always know how fresh a price is. "
                            "For a continuous feed use the WebSocket `/v1/ws` or `/v1/stream/quotes`.")
    def quotes(request: Request, symbols: str = Query(..., description="Comma separated, e.g. NSE:RELIANCE,NASDAQ:AAPL")):
        return run(request, "quotes", {"symbols": symbols})

    @router.get("/symbols/{exchange}/{ticker}", tags=["symbols"], dependencies=auth,
                summary="Symbol profile and key statistics (monetary values in the listing currency)")
    def overview(request: Request, exchange: str, ticker: str):
        return run(request, "symbol", {"symbol": f"{exchange}:{ticker}"})

    @router.get("/symbols/{exchange}/{ticker}/fundamentals", tags=["symbols"], dependencies=auth, summary="Fundamentals")
    def fundamentals(request: Request, exchange: str, ticker: str):
        return run(request, "fundamentals", {"symbol": f"{exchange}:{ticker}"})

    @router.get("/symbols/{exchange}/{ticker}/technicals", tags=["symbols"], dependencies=auth, summary="Technical indicator values")
    def technicals(request: Request, exchange: str, ticker: str, timeframe: str = "1d"):
        return run(request, "technicals", {"symbol": f"{exchange}:{ticker}", "timeframe": timeframe})

    @router.get("/symbols/{exchange}/{ticker}/candles", tags=["symbols"], dependencies=auth,
                summary="Historical OHLCV candles",
                description="Oldest first. `time` is epoch seconds (UTC). Takes a few seconds: it opens a live session upstream.")
    def candles(request: Request, exchange: str, ticker: str, timeframe: str = "1d", limit: int = Query(100, ge=1, le=5000)):
        return run(request, "candles", {"symbol": f"{exchange}:{ticker}", "timeframe": timeframe, "limit": limit})

    @router.get("/symbols/{exchange}/{ticker}/news", tags=["symbols"], dependencies=auth, summary="Latest headlines")
    def news(request: Request, exchange: str, ticker: str, limit: int = Query(20, ge=1, le=100), language: str = "en"):
        return run(request, "news", {"symbol": f"{exchange}:{ticker}", "limit": limit, "language": language})

    # ── markets ───────────────────────────────────────────────────
    @router.get("/markets/movers", tags=["markets"], dependencies=auth,
                summary="Gainers, losers, most active (liquid, main-exchange listings only)")
    def movers(request: Request, market: str = Market, category: str = "gainers", limit: int = Query(25, ge=1, le=100)):
        return run(request, "movers", {"market": market, "category": category, "limit": limit})

    @router.post("/screener", tags=["markets"], dependencies=auth, summary="Screen a market with your own conditions")
    def screener(request: Request, body: ScreenerParams):
        return run(request, "screener", body.model_dump())

    # ── calendar ──────────────────────────────────────────────────
    def calendar(kind: str):
        def handler(request: Request, markets: str = Query("india", description="Comma separated: india, america, uk, ..."),
                    date_from: Optional[str] = Query(None, alias="from", description="YYYY-MM-DD or epoch seconds. Default: today."),
                    date_to: Optional[str] = Query(None, alias="to", description="YYYY-MM-DD or epoch seconds. Default: 7 days ahead."),
                    limit: int = Query(100, ge=1, le=500)):
            return run(request, kind, {"markets": markets, "from": date_from, "to": date_to, "limit": limit})
        return handler

    router.add_api_route("/calendar/earnings", calendar("earnings"), methods=["GET"], tags=["calendar"],
                         dependencies=auth, summary="Upcoming and recent earnings")
    router.add_api_route("/calendar/dividends", calendar("dividends"), methods=["GET"], tags=["calendar"],
                         dependencies=auth, summary="Upcoming and recent dividends")

    # ── live ──────────────────────────────────────────────────────
    @router.get("/stream/quotes", tags=["live"], dependencies=auth,
                summary="Server-sent events: continuous quotes for up to 100 symbols",
                description="Prefer the WebSocket (`/v1/ws`), which also answers requests and carries more channels. "
                            "Events: `quote` (data = a quote object). Comment lines are keep-alives. "
                            "Browsers can use EventSource, but it cannot set headers: pass the key as `?api_key=`.")
    async def stream_quotes(request: Request, symbols: str = Query(...)):
        who = request.state.client
        policy = guard.policy(who)
        if not policy.allows_channel("quotes"):
            raise ApiError(403, f"The key for '{who}' is not allowed to use the live quotes feed.", code="forbidden",
                           hint="Ask the owner of the server to add 'quotes' to this client's channels.")
        ctx = Context(settings=settings, auto_resolve=False, policy=policy)
        wanted = ops.symbol_list(symbols, ctx, min(settings.ws_max_symbols_per_client, policy.max_symbols or 10 ** 6))
        slots = request.app.state.slots
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

    register_socket(router, guard, settings, version)
    return router
