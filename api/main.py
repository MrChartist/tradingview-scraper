"""Tickvale: web UI plus a production API for TradingView market data.

Run:  uvicorn api.main:app --port 8000
Docs: /docs (interactive), /redoc.  Configuration: environment variables, see .env.example.
"""
import importlib
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api import services as svc
from api.config import Settings, load_settings
from api.errors import install_handlers
from api.live import QuoteHub
from api.security import Guard
from api.v1 import StreamSlots, build_router

VERSION = "3.0.0"
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
access_log = logging.getLogger("market_terminal.access")

DESCRIPTION = """
Market data for **stocks, crypto and forex** built on public TradingView endpoints.

* **REST** under `/v1`: quotes, symbol profile, fundamentals, technicals, candles, news, movers, screener, calendar.
* **WebSocket (primary)**: `/v1/ws`. One connection to ask for anything and to receive live quotes and lists. Read the `hello` message it sends.
* **Live over SSE**: `/v1/stream/quotes` for clients that cannot use WebSockets.
* **Auth**: send `X-API-Key` (or `Authorization: Bearer`). Without configured keys the API runs in open mode.
* **Freshness**: every quote says whether it is `realtime` or `delayed` (and by how many seconds). Exchanges such as NSE and NASDAQ are typically delayed 15 minutes without a paid data licence.

Unofficial; not affiliated with TradingView. See the README for terms-of-use and licensing cautions.
"""

TAGS = [
    {"name": "help", "description": "New to markets? Start here: valid values and plain-language term meanings."},
    {"name": "quotes", "description": "Latest prices for many symbols at once."},
    {"name": "live", "description": "Continuous quotes over WebSocket or server-sent events."},
    {"name": "symbols", "description": "Per-symbol data."},
    {"name": "markets", "description": "Market-wide lists and the screener."},
    {"name": "calendar", "description": "Earnings and dividends."},
    {"name": "meta", "description": "Health and status."},
]


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or load_settings()

    # The scraper library configures root logging at DEBUG on import; apply ours.
    logging.getLogger().setLevel(settings.log_level)
    for noisy in ("urllib3", "websockets", "websocket"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    svc.CACHE_TTL = settings.cache_ttl_seconds
    for module in settings.plugins:           # fail fast: a broken plugin should stop the server, not hide
        importlib.import_module(module)
        logging.getLogger("market_terminal").info("Loaded plugin %s", module)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.hub = QuoteHub(max_symbols=settings.hub_max_upstream_symbols)
        if not settings.auth_enabled:
            logging.getLogger("market_terminal").warning(
                "API_KEYS is not set: the API is in OPEN mode. Set API_KEYS before exposing it publicly.")
        yield
        await app.state.hub.close()

    app = FastAPI(title="Tickvale API", description=DESCRIPTION, version=VERSION,
                  openapi_tags=TAGS, lifespan=lifespan)
    app.state.settings = settings
    app.state.slots = StreamSlots(settings.ws_max_clients_per_key)
    install_handlers(app)

    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST"],
                           allow_headers=["X-API-Key", "Authorization", "Content-Type"],
                           expose_headers=["X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset", "X-Request-ID"])

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = rid
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith("/v1"):
            access_log.info("%s %s -> %s %.0fms client=%s rid=%s", request.method, request.url.path, response.status_code,
                            (time.perf_counter() - started) * 1000, getattr(request.state, "client", "-"), rid)
        return response

    guard = Guard(settings)
    app.state.guard = guard
    app.include_router(build_router(guard, settings, VERSION))

    @app.get("/health", tags=["meta"], include_in_schema=False)
    def health():
        return {"status": "ok", "message": "Tickvale is running"}

    if settings.enable_web_ui:
        from api.ui import router as ui_router
        app.include_router(ui_router)
        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    return app


app = create_app()
