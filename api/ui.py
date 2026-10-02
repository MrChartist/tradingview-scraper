"""Routes used by the bundled web UI (/api/*). The product API lives in api/v1.py."""
import io
import csv
import json
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from api import services as svc

Fmt = Literal["csv", "json"]
router = APIRouter(prefix="/api", tags=["web-ui"], include_in_schema=False)

# ───────────────────────────────────────────────────────────────────
#  SYMBOL-SPECIFIC ENDPOINTS
# ───────────────────────────────────────────────────────────────────

@router.get("/search")
def search_symbols(q: str = Query(..., min_length=1, max_length=40), limit: int = Query(10, ge=1, le=30)):
    """Symbol autocomplete, so users do not need to know the exchange prefix."""
    try:
        results = svc.search_symbols_raw(q)[:limit]
    except Exception as e:
        svc.logger.warning("Symbol search failed: %s", e)
        raise HTTPException(status_code=502, detail="Symbol search is unavailable right now")
    return {"status": "success", "data": results, "total": len(results)}


@router.get("/overview/{exchange}/{ticker}")
def get_symbol_overview(exchange: str, ticker: str):
    return svc.run_scraper(lambda: svc.fetch_overview(exchange, ticker), "Symbol not found or data unavailable")


@router.get("/indicators/{exchange}/{ticker}")
def get_symbol_indicators(exchange: str, ticker: str, timeframe: str = "1d"):
    return svc.run_scraper(lambda: svc.fetch_indicators(exchange, ticker, timeframe), "Indicators not found")


@router.get("/fundamentals/{exchange}/{ticker}")
def get_symbol_fundamentals(exchange: str, ticker: str):
    return svc.run_scraper(lambda: svc.fetch_fundamentals(exchange, ticker), "Fundamental data not found")


@router.get("/ohlcv/{exchange}/{ticker}")
def get_ohlcv(
    exchange: str,
    ticker: str,
    timeframe: str = "1d",
    candles: int = Query(100, ge=5, le=5000),
):
    """Fetch historical OHLCV candle data via TradingView WebSocket."""
    def call():
        data = svc.fetch_ohlcv(exchange, ticker, timeframe, candles)
        if not data:
            return {"status": "failed", "error": "No OHLCV data returned. Check the exchange and ticker."}
        return {"status": "success", "data": data, "total": len(data)}

    return svc.run_scraper(call, "No OHLCV data returned")


# ───────────────────────────────────────────────────────────────────
#  MARKET-WIDE ENDPOINTS
# ───────────────────────────────────────────────────────────────────

@router.get("/movers")
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
    return svc.run_scraper(
        lambda: svc.cached(("movers", market, category, limit),
                       lambda: svc.movers_scraper.scrape(market=market, category=category, limit=limit)),
        "No data",
    )


@router.get("/screener")
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
    filters = svc.build_screener_filters(min_price, max_price, min_volume, min_change, max_change,
                                     min_market_cap, market, main_only)
    columns = svc.STOCK_SCREENER_COLUMNS if market in svc.STOCK_SCREENER_MARKETS else None
    key = ("screener", market, sort_by, sort_order, limit, json.dumps(filters, sort_keys=True))
    return svc.run_scraper(
        lambda: svc.cached(key, lambda: svc.screener_scraper.screen(
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

@router.get("/download/overview/{exchange}/{ticker}")
def download_overview(exchange: str, ticker: str, fmt: Fmt = "csv"):
    r = svc.run_scraper(lambda: svc.fetch_overview(exchange, ticker), "No data")
    return _send(r["data"], f"{exchange}_{ticker}_overview", fmt)


@router.get("/download/indicators/{exchange}/{ticker}")
def download_indicators(exchange: str, ticker: str, timeframe: str = "1d", fmt: Fmt = "csv"):
    r = svc.run_scraper(lambda: svc.fetch_indicators(exchange, ticker, timeframe), "No data")
    return _send(r["data"], f"{exchange}_{ticker}_{timeframe}_indicators", fmt)


@router.get("/download/fundamentals/{exchange}/{ticker}")
def download_fundamentals(exchange: str, ticker: str, fmt: Fmt = "csv"):
    r = svc.run_scraper(lambda: svc.fetch_fundamentals(exchange, ticker), "No data")
    return _send(r["data"], f"{exchange}_{ticker}_fundamentals", fmt)


@router.get("/download/ohlcv/{exchange}/{ticker}")
def download_ohlcv(
    exchange: str, ticker: str,
    timeframe: str = "1d", candles: int = Query(100, ge=5, le=5000), fmt: Fmt = "csv",
):
    r = get_ohlcv(exchange, ticker, timeframe, candles)
    return _send(r["data"], f"{exchange}_{ticker}_{timeframe}_ohlcv", fmt)


@router.get("/download/movers")
def download_movers(
    market: str = "stocks-usa", category: str = "gainers",
    limit: int = Query(50, ge=1, le=100), fmt: Fmt = "csv",
):
    r = get_market_movers(market=market, category=category, limit=limit)
    return _send(r["data"], f"{market}_{category}_movers", fmt)


@router.get("/download/screener")
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
