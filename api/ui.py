"""Routes used by the bundled web UI (/api/*). The product API lives in api/v1.py."""
import io
import csv
import json
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from api import operations as ops
from api import services as svc
from api.errors import ApiError
from api.glossary import CATALOG, GLOSSARY, FIELD_TERMS

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


@router.get("/glossary")
def glossary():
    return {"status": "success", "terms": GLOSSARY, "field_terms": FIELD_TERMS, "catalog": CATALOG}


@router.get("/resolve")
def resolve(request: Request, q: str = Query(..., min_length=1, max_length=60)):
    try:
        return {"status": "success", "data": svc.resolve_symbol(q, request.app.state.settings.default_country)}
    except Exception as e:
        svc.logger.warning("Resolve failed: %s", e)
        raise HTTPException(status_code=502, detail="Search is unavailable right now.")


@router.get("/quote/{exchange}/{ticker}")
def quote(exchange: str, ticker: str):
    svc.check_symbol_parts(exchange, ticker)
    quotes, _ = svc.snapshot_quotes([f"{exchange.upper()}:{ticker.upper()}"])
    if not quotes:
        raise HTTPException(status_code=404, detail="Symbol not found")
    return {"status": "success", "data": quotes[0]}


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


# ───────────────────────────────────────────────────────────────────
#  STRATEGY TEST (historical backtest, and paper tests that keep running)
#  Paper tests from the web page are only allowed on a server without API keys (your own machine or
#  private network): on a public server anyone could start them, so use the keyed /v1 API there.
# ───────────────────────────────────────────────────────────────────
def _test_ctx(request: Request) -> ops.Context:
    st = request.app.state
    return ops.Context(settings=st.settings, hub=st.hub, auto_resolve=True, owner="web", paper=getattr(st, "paper", None))


def _run(request: Request, name: str, params: dict) -> dict:
    try:
        r = ops.execute(name, params, _test_ctx(request))
    except ApiError as e:
        raise HTTPException(status_code=400, detail=f"{e.message} {e.hint or ''}".strip())
    return {"status": "success", "data": r.data, "meta": r.meta}


def _local_only(request: Request) -> None:
    if request.app.state.settings.auth_enabled:
        raise HTTPException(status_code=400, detail="Paper tests are switched off on the public page. Use the API with your key (POST /v1/paper).")


@router.post("/backtest")
def ui_backtest(request: Request, body: ops.BacktestParams):
    return _run(request, "backtest", body.model_dump())


@router.get("/paper")
def ui_paper_list(request: Request):
    _local_only(request)
    return _run(request, "paper_list", {})


@router.post("/paper")
def ui_paper_start(request: Request, body: ops.PaperStartParams):
    _local_only(request)
    return _run(request, "paper_start", body.model_dump())


@router.delete("/paper/{run_id}")
def ui_paper_stop(request: Request, run_id: str, delete: bool = False):
    _local_only(request)
    return _run(request, "paper_stop", {"id": run_id, "delete": delete})
