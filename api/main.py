import io
import csv
import json
import logging
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

# Import scraper modules
from tradingview_scraper.symbols.overview import Overview
from tradingview_scraper.symbols.technicals import Indicators
from tradingview_scraper.symbols.fundamental_graphs import FundamentalGraphs
from tradingview_scraper.symbols.market_movers import MarketMovers
from tradingview_scraper.symbols.screener import Screener

app = FastAPI(
    title="TradingView Scraper API",
    description="A comprehensive API wrapper around the tradingview-scraper package.",
    version="2.0.0"
)

# ─── Static File Routes ────────────────────────────────────────────
@app.get("/")
def read_root():
    return FileResponse("frontend/index.html")

@app.get("/style.css")
def read_style():
    return FileResponse("frontend/style.css")

@app.get("/script.js")
def read_script():
    return FileResponse("frontend/script.js")


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


# ───────────────────────────────────────────────────────────────────
#  SYMBOL-SPECIFIC ENDPOINTS
# ───────────────────────────────────────────────────────────────────

@app.get("/api/overview/{exchange}/{ticker}")
def get_symbol_overview(exchange: str, ticker: str):
    symbol = f"{exchange.upper()}:{ticker.upper()}"
    try:
        response = overview_scraper.get_symbol_overview(symbol=symbol)
        if response.get("status") == "success":
            return response
        raise HTTPException(status_code=404, detail="Symbol not found or data unavailable")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/indicators/{exchange}/{ticker}")
def get_symbol_indicators(exchange: str, ticker: str, timeframe: str = "1d"):
    try:
        response = indicators_scraper.scrape(
            exchange=exchange.upper(),
            symbol=ticker.upper(),
            timeframe=timeframe,
            allIndicators=True
        )
        if response.get("status") == "success":
            return response
        raise HTTPException(status_code=404, detail="Indicators not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/fundamentals/{exchange}/{ticker}")
def get_symbol_fundamentals(exchange: str, ticker: str):
    symbol = f"{exchange.upper()}:{ticker.upper()}"
    try:
        response = fundamentals_scraper.get_fundamentals(symbol=symbol)
        if response.get("status") == "success":
            return response
        raise HTTPException(status_code=404, detail="Fundamental data not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/ohlcv/{exchange}/{ticker}")
def get_ohlcv(
    exchange: str,
    ticker: str,
    timeframe: str = "1d",
    candles: int = 100
):
    """Fetch historical OHLCV candle data via TradingView WebSocket."""
    try:
        from tradingview_scraper.symbols.stream import Streamer
        streamer = Streamer(export_result=True, export_type='json')
        result = streamer.stream(
            exchange=exchange.upper(),
            symbol=ticker.upper(),
            timeframe=timeframe,
            numb_price_candles=candles
        )
        ohlc_data = result.get("ohlc", [])
        if not ohlc_data:
            raise HTTPException(status_code=404, detail="No OHLCV data returned")
        return {"status": "success", "data": ohlc_data, "total": len(ohlc_data)}
    except HTTPException:
        raise
    except Exception as e:
        logging.exception("OHLCV fetch failed")
        raise HTTPException(status_code=500, detail=str(e))


# ───────────────────────────────────────────────────────────────────
#  MARKET-WIDE ENDPOINTS
# ───────────────────────────────────────────────────────────────────

@app.get("/api/movers")
def get_market_movers(
    market: str = "stocks-usa",
    category: str = "gainers",
    limit: int = 25
):
    """
    Get market movers: gainers, losers, most-active, penny-stocks, etc.
    Markets: stocks-usa, stocks-india, stocks-uk, crypto, forex, futures, bonds
    Categories: gainers, losers, most-active, penny-stocks, pre-market-gainers, etc.
    """
    try:
        response = movers_scraper.scrape(
            market=market,
            category=category,
            limit=limit
        )
        if response.get("status") == "success":
            return response
        raise HTTPException(status_code=404, detail=response.get("error", "No data"))
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/screener")
def screen_market(
    market: str = "america",
    sort_by: str = "volume",
    sort_order: str = "desc",
    limit: int = 25,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    min_volume: Optional[float] = None,
    min_change: Optional[float] = None,
    max_change: Optional[float] = None,
    min_market_cap: Optional[float] = None,
):
    """
    Screen stocks/crypto/forex with custom filters.
    Markets: america, india, uk, crypto, forex, global, etc.
    """
    filters = []
    if min_price is not None:
        filters.append({"left": "close", "operation": "egreater", "right": min_price})
    if max_price is not None:
        filters.append({"left": "close", "operation": "eless", "right": max_price})
    if min_volume is not None:
        filters.append({"left": "volume", "operation": "egreater", "right": min_volume})
    if min_change is not None:
        filters.append({"left": "change", "operation": "egreater", "right": min_change})
    if max_change is not None:
        filters.append({"left": "change", "operation": "eless", "right": max_change})
    if min_market_cap is not None:
        filters.append({"left": "market_cap_basic", "operation": "egreater", "right": min_market_cap})

    try:
        response = screener_scraper.screen(
            market=market,
            filters=filters if filters else None,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit
        )
        if response.get("status") == "success":
            return response
        raise HTTPException(status_code=404, detail=response.get("error", "No data"))
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ───────────────────────────────────────────────────────────────────
#  DOWNLOAD HELPERS
# ───────────────────────────────────────────────────────────────────

def _dict_to_csv_stream(data: dict, filename: str) -> StreamingResponse:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Field", "Value"])
    for k, v in data.items():
        if isinstance(v, (dict, list)):
            v = json.dumps(v)
        writer.writerow([k, v])
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


def _list_to_csv_stream(data: list, filename: str) -> StreamingResponse:
    output = io.StringIO()
    if data:
        keys = list(data[0].keys())
        writer = csv.DictWriter(output, fieldnames=keys)
        writer.writeheader()
        for row in data:
            writer.writerow({k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in row.items()})
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


def _to_json_stream(data, filename: str) -> StreamingResponse:
    content = json.dumps(data, indent=2, default=str)
    return StreamingResponse(
        io.BytesIO(content.encode()),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# ───────────────────────────────────────────────────────────────────
#  DOWNLOAD ENDPOINTS
# ───────────────────────────────────────────────────────────────────

@app.get("/api/download/overview/{exchange}/{ticker}")
def download_overview(exchange: str, ticker: str, fmt: str = "csv"):
    symbol = f"{exchange.upper()}:{ticker.upper()}"
    response = overview_scraper.get_symbol_overview(symbol=symbol)
    if response.get("status") != "success":
        raise HTTPException(status_code=404, detail="No data")
    fname = f"{exchange}_{ticker}_overview"
    if fmt == "json":
        return _to_json_stream(response["data"], f"{fname}.json")
    return _dict_to_csv_stream(response["data"], f"{fname}.csv")


@app.get("/api/download/indicators/{exchange}/{ticker}")
def download_indicators(exchange: str, ticker: str, fmt: str = "csv"):
    response = indicators_scraper.scrape(
        exchange=exchange.upper(), symbol=ticker.upper(),
        timeframe="1d", allIndicators=True
    )
    if response.get("status") != "success":
        raise HTTPException(status_code=404, detail="No data")
    fname = f"{exchange}_{ticker}_indicators"
    if fmt == "json":
        return _to_json_stream(response["data"], f"{fname}.json")
    return _dict_to_csv_stream(response["data"], f"{fname}.csv")


@app.get("/api/download/fundamentals/{exchange}/{ticker}")
def download_fundamentals(exchange: str, ticker: str, fmt: str = "csv"):
    symbol = f"{exchange.upper()}:{ticker.upper()}"
    response = fundamentals_scraper.get_fundamentals(symbol=symbol)
    if response.get("status") != "success":
        raise HTTPException(status_code=404, detail="No data")
    fname = f"{exchange}_{ticker}_fundamentals"
    if fmt == "json":
        return _to_json_stream(response["data"], f"{fname}.json")
    return _dict_to_csv_stream(response["data"], f"{fname}.csv")


@app.get("/api/download/ohlcv/{exchange}/{ticker}")
def download_ohlcv(
    exchange: str, ticker: str,
    timeframe: str = "1d", candles: int = 100, fmt: str = "csv"
):
    try:
        from tradingview_scraper.symbols.stream import Streamer
        streamer = Streamer(export_result=True, export_type='json')
        result = streamer.stream(
            exchange=exchange.upper(), symbol=ticker.upper(),
            timeframe=timeframe, numb_price_candles=candles
        )
        ohlc_data = result.get("ohlc", [])
        if not ohlc_data:
            raise HTTPException(status_code=404, detail="No OHLCV data")
        fname = f"{exchange}_{ticker}_{timeframe}_ohlcv"
        if fmt == "json":
            return _to_json_stream(ohlc_data, f"{fname}.json")
        return _list_to_csv_stream(ohlc_data, f"{fname}.csv")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/download/movers")
def download_movers(
    market: str = "stocks-usa", category: str = "gainers",
    limit: int = 50, fmt: str = "csv"
):
    response = movers_scraper.scrape(market=market, category=category, limit=limit)
    if response.get("status") != "success":
        raise HTTPException(status_code=404, detail="No data")
    fname = f"{market}_{category}_movers"
    if fmt == "json":
        return _to_json_stream(response["data"], f"{fname}.json")
    return _list_to_csv_stream(response["data"], f"{fname}.csv")


@app.get("/api/download/screener")
def download_screener(
    market: str = "america", sort_by: str = "volume",
    sort_order: str = "desc", limit: int = 50, fmt: str = "csv"
):
    response = screener_scraper.screen(
        market=market, sort_by=sort_by, sort_order=sort_order, limit=limit
    )
    if response.get("status") != "success":
        raise HTTPException(status_code=404, detail="No data")
    fname = f"{market}_screener"
    if fmt == "json":
        return _to_json_stream(response["data"], f"{fname}.json")
    return _list_to_csv_stream(response["data"], f"{fname}.csv")
