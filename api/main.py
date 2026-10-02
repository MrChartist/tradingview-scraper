import io
import csv
import json
import time
import logging
import re
import threading
from pathlib import Path
from typing import Any, Callable, Literal, Optional

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# Import scraper modules
from tradingview_scraper.symbols.overview import Overview
from tradingview_scraper.symbols.technicals import Indicators
from tradingview_scraper.symbols.fundamental_graphs import FundamentalGraphs
from tradingview_scraper.symbols.market_movers import MarketMovers
from tradingview_scraper.symbols.screener import Screener
from tradingview_scraper.symbols.stream import Streamer

logger = logging.getLogger("terminal")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

VALID_TIMEFRAMES = {"1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w", "1M"}
Fmt = Literal["csv", "json"]

app = FastAPI(
    title="TradingView Intelligence Terminal API",
    description="A REST API and web terminal built on the tradingview-scraper package.",
    version="2.1.0",
)


# ─── Tiny TTL cache ────────────────────────────────────────────────
# Repeated clicks, tab switches and downloads reuse the last response
# instead of hitting TradingView again.
_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()
CACHE_TTL = 60  # seconds
CACHE_MAX = 256


def cached(key: tuple, producer: Callable[[], Any], ttl: int = CACHE_TTL):
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = producer()
    with _CACHE_LOCK:
        if len(_CACHE) >= CACHE_MAX:
            oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
            _CACHE.pop(oldest, None)
        _CACHE[key] = (now, value)
    return value


def check_timeframe(timeframe: str) -> str:
    if timeframe not in VALID_TIMEFRAMES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid timeframe '{timeframe}'. Use one of: {', '.join(sorted(VALID_TIMEFRAMES))}",
        )
    return timeframe


class _InMemoryStreamer(Streamer):
    """Streamer that returns collected candles without writing export files.

    Streamer only collects candles into a dict when export_result=True, and that
    mode also saves a file on every call. The API only needs the data.
    """

    def _export(self, *args, **kwargs):  # noqa: D102
        return None


STOCK_SCREENER_MARKETS = {"america", "india", "uk", "canada", "germany", "australia", "global"}
STOCK_SCREENER_COLUMNS = [
    "name", "description", "close", "change", "change_abs", "volume", "Recommend.All",
    "market_cap_basic", "price_earnings_ttm", "earnings_per_share_basic_ttm", "currency",
]


# ─── Initialize Scrapers ───────────────────────────────────────────
overview_scraper = Overview()
indicators_scraper = Indicators()
fundamentals_scraper = FundamentalGraphs()
movers_scraper = MarketMovers()
screener_scraper = Screener()


# ─── Health ────────────────────────────────────────────────────────
@app.get("/health")
def health_check():
    return {"status": "ok", "message": "TradingView Intelligence Terminal is running"}


# ─── Native-currency values ────────────────────────────────────────
# The symbol endpoints report every monetary figure in USD. The regional
# scanners report them in the listing currency (rupees for NSE/BSE), so for
# monetary fields we replace the USD figure with the native one.
EXCHANGE_REGION = {
    "NSE": "india", "BSE": "india",
    "NASDAQ": "america", "NYSE": "america", "AMEX": "america", "NYSE ARCA": "america", "ARCA": "america",
    "LSE": "uk", "TSX": "canada", "TSXV": "canada", "ASX": "australia",
    "XETR": "germany", "FWB": "germany", "SWB": "germany",
}
_MONEY_NAME = re.compile(
    r"market_cap|earnings_per_share|eps|per_share|revenue|income|profit|^cash_|debt|assets|ebitda|"
    r"enterprise_value|free_cash_flow|^Value\.Traded$|ebit$", re.I)
_NOT_MONEY = re.compile(r"margin|ratio|_to_|return_on|percent|yield|payout|growth|^debt_to|^current_|^quick_", re.I)


def is_money_field(name: str) -> bool:
    return bool(_MONEY_NAME.search(name)) and not _NOT_MONEY.search(name)


def with_native_currency(exchange: str, ticker: str, response: dict) -> dict:
    """Return a copy of a scraper response with monetary fields in the listing currency."""
    data = dict(response.get("data") or {})
    region = EXCHANGE_REGION.get(exchange.upper())
    money = [k for k in data if is_money_field(k) and isinstance(data[k], (int, float))]
    currency = "USD"
    if region and money:
        symbol = f"{exchange.upper()}:{ticker.upper()}"
        columns = money + ["currency", "fundamental_currency_code"]
        try:
            def produce():
                r = requests.post(
                    f"https://scanner.tradingview.com/{region}/scan",
                    json={"symbols": {"tickers": [symbol]}, "columns": columns},
                    timeout=10,
                )
                r.raise_for_status()
                rows = r.json().get("data") or []
                return dict(zip(columns, rows[0]["d"])) if rows else {}

            native = cached(("native", symbol, tuple(money)), produce)
            if native:
                for k in money:
                    if isinstance(native.get(k), (int, float)):
                        data[k] = native[k]
                currency = native.get("fundamental_currency_code") or native.get("currency") or currency
                data["price_currency"] = native.get("currency") or currency
        except Exception as e:  # keep USD figures rather than failing the request
            logger.warning("Native currency lookup failed for %s:%s: %s", exchange, ticker, e)
    data["currency"] = currency
    return {**response, "data": data}


# ─── Data fetch helpers (shared by JSON and download endpoints) ────
def fetch_overview(exchange: str, ticker: str) -> dict:
    symbol = f"{exchange.upper()}:{ticker.upper()}"
    def produce():
        resp = overview_scraper.get_symbol_overview(symbol=symbol)
        return with_native_currency(exchange, ticker, resp) if resp.get("status") == "success" else resp

    return cached(("overview", symbol), produce)


def fetch_indicators(exchange: str, ticker: str, timeframe: str) -> dict:
    check_timeframe(timeframe)
    key = ("indicators", exchange.upper(), ticker.upper(), timeframe)
    return cached(key, lambda: indicators_scraper.scrape(
        exchange=exchange.upper(), symbol=ticker.upper(),
        timeframe=timeframe, allIndicators=True,
    ))


def fetch_fundamentals(exchange: str, ticker: str) -> dict:
    symbol = f"{exchange.upper()}:{ticker.upper()}"
    def produce():
        resp = fundamentals_scraper.get_fundamentals(symbol=symbol)
        return with_native_currency(exchange, ticker, resp) if resp.get("status") == "success" else resp

    return cached(("fundamentals", symbol), produce)


def fetch_ohlcv(exchange: str, ticker: str, timeframe: str, candles: int) -> list:
    check_timeframe(timeframe)
    key = ("ohlcv", exchange.upper(), ticker.upper(), timeframe, candles)

    def produce():
        streamer = _InMemoryStreamer(export_result=True)
        result = streamer.stream(
            exchange=exchange.upper(), symbol=ticker.upper(),
            timeframe=timeframe, numb_price_candles=candles,
        )
        return result.get("ohlc", [])

    return cached(key, produce)


# Main listing venue per screener market (avoids OTC names and NSE/BSE double listings).
MAIN_EXCHANGES = {
    "india": ["NSE"], "america": ["NASDAQ", "NYSE", "AMEX"], "uk": ["LSE"],
    "canada": ["TSX"], "germany": ["XETR"], "australia": ["ASX"],
}


def build_screener_filters(min_price, max_price, min_volume, min_change, max_change, min_market_cap,
                           market=None, main_only=False):
    spec = [
        ("close", "egreater", min_price), ("close", "eless", max_price),
        ("volume", "egreater", min_volume),
        ("change", "egreater", min_change), ("change", "eless", max_change),
        ("market_cap_basic", "egreater", min_market_cap),
    ]
    filters = [{"left": f, "operation": op, "right": v} for f, op, v in spec if v is not None]
    if main_only and market in MAIN_EXCHANGES:
        filters.append({"left": "exchange", "operation": "in_range", "right": MAIN_EXCHANGES[market]})
    return filters


def run_scraper(call: Callable[[], dict], not_found: str) -> dict:
    """Run a scraper call and translate failures into clean HTTP errors."""
    try:
        response = call()
        if response.get("status") == "success":
            return response
        raise HTTPException(status_code=404, detail=response.get("error") or not_found)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Scraper call failed")
        raise HTTPException(status_code=500, detail=str(e))


# ───────────────────────────────────────────────────────────────────
#  SYMBOL-SPECIFIC ENDPOINTS
# ───────────────────────────────────────────────────────────────────

@app.get("/api/search")
def search_symbols(q: str = Query(..., min_length=1, max_length=40), limit: int = Query(10, ge=1, le=30)):
    """Symbol autocomplete, so users do not need to know the exchange prefix."""
    def produce():
        resp = requests.get(
            "https://symbol-search.tradingview.com/symbol_search/v3/",
            params={"text": q, "hl": 0, "lang": "en", "domain": "production"},
            headers={"Origin": "https://www.tradingview.com", "Referer": "https://www.tradingview.com/"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("symbols", [])

    try:
        raw = cached(("search", q.lower()), produce, ttl=300)
    except Exception as e:
        logger.warning("Symbol search failed: %s", e)
        raise HTTPException(status_code=502, detail="Symbol search is unavailable right now")
    results = [
        {
            "exchange": r.get("exchange"),
            "symbol": r.get("symbol"),
            "description": r.get("description"),
            "type": r.get("type"),
            "country": r.get("country"),
            "currency": r.get("currency_code"),
        }
        for r in raw
        if r.get("symbol") and r.get("exchange")
    ]
    return {"status": "success", "data": results[:limit], "total": len(results[:limit])}


@app.get("/api/overview/{exchange}/{ticker}")
def get_symbol_overview(exchange: str, ticker: str):
    return run_scraper(lambda: fetch_overview(exchange, ticker), "Symbol not found or data unavailable")


@app.get("/api/indicators/{exchange}/{ticker}")
def get_symbol_indicators(exchange: str, ticker: str, timeframe: str = "1d"):
    return run_scraper(lambda: fetch_indicators(exchange, ticker, timeframe), "Indicators not found")


@app.get("/api/fundamentals/{exchange}/{ticker}")
def get_symbol_fundamentals(exchange: str, ticker: str):
    return run_scraper(lambda: fetch_fundamentals(exchange, ticker), "Fundamental data not found")


@app.get("/api/ohlcv/{exchange}/{ticker}")
def get_ohlcv(
    exchange: str,
    ticker: str,
    timeframe: str = "1d",
    candles: int = Query(100, ge=5, le=5000),
):
    """Fetch historical OHLCV candle data via TradingView WebSocket."""
    def call():
        data = fetch_ohlcv(exchange, ticker, timeframe, candles)
        if not data:
            return {"status": "failed", "error": "No OHLCV data returned. Check the exchange and ticker."}
        return {"status": "success", "data": data, "total": len(data)}

    return run_scraper(call, "No OHLCV data returned")


# ───────────────────────────────────────────────────────────────────
#  MARKET-WIDE ENDPOINTS
# ───────────────────────────────────────────────────────────────────

@app.get("/api/movers")
def get_market_movers(
    market: str = "stocks-usa",
    category: str = "gainers",
    limit: int = Query(25, ge=1, le=100),
):
    """
    Get market movers: gainers, losers, most-active, penny-stocks, etc.
    Markets: stocks-usa, stocks-india, stocks-uk, stocks-canada, stocks-australia, crypto, forex.
    Pre-market / after-hours categories are available for stocks-usa only.
    """
    return run_scraper(
        lambda: cached(("movers", market, category, limit),
                       lambda: movers_scraper.scrape(market=market, category=category, limit=limit)),
        "No data",
    )


@app.get("/api/screener")
def screen_market(
    market: str = "america",
    sort_by: str = "volume",
    sort_order: Literal["asc", "desc"] = "desc",
    limit: int = Query(25, ge=1, le=200),
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    min_volume: Optional[float] = None,
    min_change: Optional[float] = None,
    max_change: Optional[float] = None,
    min_market_cap: Optional[float] = None,
    main_only: bool = True,
):
    """Screen stocks/crypto/forex with custom filters. Markets: america, india, uk, crypto, forex, global, etc.

    main_only keeps results to the main exchange(s) of the market (for example NSE for India).
    """
    filters = build_screener_filters(min_price, max_price, min_volume, min_change, max_change,
                                     min_market_cap, market, main_only)
    columns = STOCK_SCREENER_COLUMNS if market in STOCK_SCREENER_MARKETS else None
    key = ("screener", market, sort_by, sort_order, limit, json.dumps(filters, sort_keys=True))
    return run_scraper(
        lambda: cached(key, lambda: screener_scraper.screen(
            market=market, filters=filters or None, columns=columns,
            sort_by=sort_by, sort_order=sort_order, limit=limit)),
        "No data",
    )


# ───────────────────────────────────────────────────────────────────
#  DOWNLOAD HELPERS
# ───────────────────────────────────────────────────────────────────

def _csv_safe(value):
    """Serialise nested values and neutralise spreadsheet formula injection."""
    if isinstance(value, (dict, list)):
        value = json.dumps(value)
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        try:
            float(value)
        except ValueError:
            value = "'" + value
    return value


def _attachment(filename: str) -> dict:
    return {"Content-Disposition": f'attachment; filename="{filename}"'}


def _dict_to_csv_stream(data: dict, filename: str) -> StreamingResponse:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Field", "Value"])
    for k, v in data.items():
        writer.writerow([k, _csv_safe(v)])
    output.seek(0)
    return StreamingResponse(output, media_type="text/csv", headers=_attachment(filename))


def _list_to_csv_stream(data: list, filename: str) -> StreamingResponse:
    output = io.StringIO()
    if data:
        keys = list(dict.fromkeys(k for row in data for k in row))
        writer = csv.DictWriter(output, fieldnames=keys)
        writer.writeheader()
        for row in data:
            writer.writerow({k: _csv_safe(v) for k, v in row.items()})
    output.seek(0)
    return StreamingResponse(output, media_type="text/csv", headers=_attachment(filename))


def _to_json_stream(data, filename: str) -> StreamingResponse:
    content = json.dumps(data, indent=2, default=str)
    return StreamingResponse(io.BytesIO(content.encode()), media_type="application/json",
                             headers=_attachment(filename))


def _send(data, filename: str, fmt: str) -> StreamingResponse:
    if fmt == "json":
        return _to_json_stream(data, f"{filename}.json")
    if isinstance(data, dict):
        return _dict_to_csv_stream(data, f"{filename}.csv")
    return _list_to_csv_stream(data, f"{filename}.csv")


# ───────────────────────────────────────────────────────────────────
#  DOWNLOAD ENDPOINTS
# ───────────────────────────────────────────────────────────────────

@app.get("/api/download/overview/{exchange}/{ticker}")
def download_overview(exchange: str, ticker: str, fmt: Fmt = "csv"):
    r = run_scraper(lambda: fetch_overview(exchange, ticker), "No data")
    return _send(r["data"], f"{exchange}_{ticker}_overview", fmt)


@app.get("/api/download/indicators/{exchange}/{ticker}")
def download_indicators(exchange: str, ticker: str, timeframe: str = "1d", fmt: Fmt = "csv"):
    r = run_scraper(lambda: fetch_indicators(exchange, ticker, timeframe), "No data")
    return _send(r["data"], f"{exchange}_{ticker}_{timeframe}_indicators", fmt)


@app.get("/api/download/fundamentals/{exchange}/{ticker}")
def download_fundamentals(exchange: str, ticker: str, fmt: Fmt = "csv"):
    r = run_scraper(lambda: fetch_fundamentals(exchange, ticker), "No data")
    return _send(r["data"], f"{exchange}_{ticker}_fundamentals", fmt)


@app.get("/api/download/ohlcv/{exchange}/{ticker}")
def download_ohlcv(
    exchange: str, ticker: str,
    timeframe: str = "1d", candles: int = Query(100, ge=5, le=5000), fmt: Fmt = "csv",
):
    r = get_ohlcv(exchange, ticker, timeframe, candles)
    return _send(r["data"], f"{exchange}_{ticker}_{timeframe}_ohlcv", fmt)


@app.get("/api/download/movers")
def download_movers(
    market: str = "stocks-usa", category: str = "gainers",
    limit: int = Query(50, ge=1, le=100), fmt: Fmt = "csv",
):
    r = get_market_movers(market=market, category=category, limit=limit)
    return _send(r["data"], f"{market}_{category}_movers", fmt)


@app.get("/api/download/screener")
def download_screener(
    market: str = "america", sort_by: str = "volume",
    sort_order: Literal["asc", "desc"] = "desc", limit: int = Query(50, ge=1, le=200),
    min_price: Optional[float] = None, max_price: Optional[float] = None,
    min_volume: Optional[float] = None, min_change: Optional[float] = None,
    max_change: Optional[float] = None, min_market_cap: Optional[float] = None,
    main_only: bool = True, fmt: Fmt = "csv",
):
    r = screen_market(market, sort_by, sort_order, limit, min_price, max_price,
                      min_volume, min_change, max_change, min_market_cap, main_only)
    return _send(r["data"], f"{market}_screener", fmt)


# ─── Frontend (mounted last so /api and /health take priority) ─────
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
