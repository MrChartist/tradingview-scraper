import time
import logging
import re
import threading
from typing import Any, Callable, Optional

import requests
from fastapi import HTTPException

# Import scraper modules
from tradingview_scraper.symbols.overview import Overview
from tradingview_scraper.symbols.technicals import Indicators
from tradingview_scraper.symbols.fundamental_graphs import FundamentalGraphs
from tradingview_scraper.symbols.market_movers import MarketMovers
from tradingview_scraper.symbols.screener import Screener
from tradingview_scraper.symbols.stream import Streamer

logger = logging.getLogger("market_terminal")

VALID_TIMEFRAMES = {"1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w", "1M"}


# ─── Tiny TTL cache ────────────────────────────────────────────────
# Repeated clicks, tab switches and downloads reuse the last response
# instead of hitting TradingView again.
_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()
CACHE_TTL = 60  # seconds
CACHE_MAX = 256


def cached(key: tuple, producer: Callable[[], Any], ttl: Optional[int] = None):
    ttl = CACHE_TTL if ttl is None else ttl
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


_PART_RE = re.compile(r"^[A-Za-z0-9_.!&\-/ ]{1,40}$")


def check_symbol_parts(exchange: str, ticker: str) -> None:
    """Reject anything that could not be a real exchange / ticker before it reaches an upstream URL."""
    if not _PART_RE.match(exchange) or not _PART_RE.match(ticker) or ".." in exchange + ticker:
        raise HTTPException(status_code=400, detail="Invalid exchange or ticker. Use letters and numbers, like NSE and RELIANCE.")


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
    check_symbol_parts(exchange, ticker)
    symbol = f"{exchange.upper()}:{ticker.upper()}"
    def produce():
        resp = overview_scraper.get_symbol_overview(symbol=symbol)
        return with_native_currency(exchange, ticker, resp) if resp.get("status") == "success" else resp

    return cached(("overview", symbol), produce)


def fetch_indicators(exchange: str, ticker: str, timeframe: str) -> dict:
    check_symbol_parts(exchange, ticker)
    check_timeframe(timeframe)
    key = ("indicators", exchange.upper(), ticker.upper(), timeframe)
    return cached(key, lambda: indicators_scraper.scrape(
        exchange=exchange.upper(), symbol=ticker.upper(),
        timeframe=timeframe, allIndicators=True,
    ))


def fetch_fundamentals(exchange: str, ticker: str) -> dict:
    check_symbol_parts(exchange, ticker)
    symbol = f"{exchange.upper()}:{ticker.upper()}"
    def produce():
        resp = fundamentals_scraper.get_fundamentals(symbol=symbol)
        return with_native_currency(exchange, ticker, resp) if resp.get("status") == "success" else resp

    return cached(("fundamentals", symbol), produce)


def fetch_ohlcv(exchange: str, ticker: str, timeframe: str, candles: int) -> list:
    check_symbol_parts(exchange, ticker)
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



def search_symbols_raw(q: str) -> list:
    """Symbol autocomplete results straight from TradingView (cached 5 minutes)."""
    def produce():
        resp = requests.get(
            "https://symbol-search.tradingview.com/symbol_search/v3/",
            params={"text": q, "hl": 0, "lang": "en", "domain": "production"},
            headers={"Origin": "https://www.tradingview.com", "Referer": "https://www.tradingview.com/"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("symbols", [])

    raw = cached(("search", q.lower()), produce, ttl=300)
    return [
        {
            "exchange": r.get("exchange"),
            "symbol": r.get("symbol"),
            "description": r.get("description"),
            "type": r.get("type"),
            "country": r.get("country"),
            "currency": r.get("currency_code"),
            "primary": bool(r.get("is_primary_listing")),
        }
        for r in raw
        if r.get("symbol") and r.get("exchange")
    ]


# ─── Quote snapshots (batch) ───────────────────────────────────────
SNAPSHOT_COLUMNS = [
    "close", "change", "change_abs", "open", "high", "low", "volume", "market_cap_basic", "currency",
    "description", "name", "exchange", "type", "update_mode", "bid", "ask",
    "price_52_week_high", "price_52_week_low", "sector", "industry",
]
from api.live import SYMBOL_RE as SYMBOL_PATTERN, delay_info  # noqa: E402




def snapshot_quotes(symbols: list) -> tuple:
    """Latest quote for many symbols in a few requests. Returns (quotes, not_found)."""
    by_region: dict = {}
    for sym in symbols:
        by_region.setdefault(EXCHANGE_REGION.get(sym.split(":")[0], "global"), []).append(sym)

    found: dict = {}
    for region, tickers in by_region.items():
        def produce(region=region, tickers=tickers):
            r = requests.post(
                f"https://scanner.tradingview.com/{region}/scan",
                json={"symbols": {"tickers": tickers}, "columns": SNAPSHOT_COLUMNS},
                timeout=10,
            )
            r.raise_for_status()
            return r.json().get("data", [])

        for row in cached(("snapshot", region, tuple(sorted(tickers))), produce, ttl=5):
            d = dict(zip(SNAPSHOT_COLUMNS, row["d"]))
            found[row["s"]] = {
                "symbol": row["s"], "status": "ok",
                "price": d["close"], "change": d["change_abs"], "change_percent": d["change"],
                "open": d["open"], "high": d["high"], "low": d["low"], "volume": d["volume"],
                "bid": d["bid"], "ask": d["ask"], "currency": d["currency"], "name": d["name"],
                "description": d["description"], "exchange": d["exchange"], "type": d["type"],
                "market_cap": d["market_cap_basic"],
                "high_52w": d["price_52_week_high"], "low_52w": d["price_52_week_low"],
                "sector": d["sector"], "industry": d["industry"],
                "update_mode": d["update_mode"], **delay_info(d["update_mode"]),
            }
    quotes = [found[s] for s in symbols if s in found]
    return quotes, [s for s in symbols if s not in found]


# ─── News ──────────────────────────────────────────────────────────
def _absolute(path: Optional[str]) -> Optional[str]:
    return f"https://www.tradingview.com{path}" if path and path.startswith("/") else path


def fetch_news(exchange: str, ticker: str, limit: int, language: str = "en") -> list:
    check_symbol_parts(exchange, ticker)
    from tradingview_scraper.symbols.news import NewsScraper

    def produce():
        return NewsScraper().scrape_headlines(symbol=ticker.upper(), exchange=exchange.upper(),
                                              sort="latest", language=language)

    items = cached(("news", exchange.upper(), ticker.upper(), language), produce, ttl=120)
    return [
        {
            "id": n.get("id"), "title": n.get("title"), "provider": n.get("provider"),
            "source": n.get("source"), "published_at": n.get("published"),
            "url": _absolute(n.get("link") or n.get("storyPath")),
        }
        for n in (items or [])[:limit]
    ]


# ─── Event calendar (explicit column mapping) ──────────────────────
CALENDAR_COLUMNS = {
    "earnings": {
        "url": "https://scanner.tradingview.com/global/scan?label-product=calendar-earnings",
        "date_field": "earnings_release_date,earnings_release_next_date",
        "columns": ["name", "description", "earnings_release_date", "earnings_release_next_date",
                    "earnings_release_time", "earnings_per_share_fq", "earnings_per_share_forecast_fq",
                    "earnings_per_share_forecast_next_fq", "eps_surprise_fq", "eps_surprise_percent_fq",
                    "revenue_fq", "revenue_forecast_fq", "revenue_forecast_next_fq", "market_cap_basic",
                    "fundamental_currency_code", "market"],
    },
    "dividends": {
        "url": "https://scanner.tradingview.com/global/scan?label-product=calendar-dividends",
        "date_field": "dividend_ex_date_recent,dividend_ex_date_upcoming",
        "columns": ["name", "description", "dividend_ex_date_recent", "dividend_ex_date_upcoming",
                    "dividend_payment_date_recent", "dividend_payment_date_upcoming",
                    "dividend_amount_recent", "dividend_amount_upcoming", "dividends_yield",
                    "market_cap_basic", "fundamental_currency_code", "market"],
    },
}


def fetch_calendar(kind: str, markets: list, ts_from: int, ts_to: int, limit: int) -> list:
    spec = CALENDAR_COLUMNS[kind]
    payload = {
        "filter": [{"left": spec["date_field"], "operation": "in_range", "right": [ts_from, ts_to]}],
        "columns": spec["columns"], "options": {"lang": "en"}, "range": [0, limit],
        "sort": {"sortBy": spec["columns"][2], "sortOrder": "asc"},
    }
    if markets:
        payload["markets"] = markets
        if all(m in MAIN_EXCHANGES for m in markets):     # one row per company, not per listing
            exchanges = [e for m in markets for e in MAIN_EXCHANGES[m]]
            payload["filter"].append({"left": "exchange", "operation": "in_range", "right": exchanges})

    def produce():
        r = requests.post(spec["url"], json=payload, timeout=10)
        r.raise_for_status()
        return r.json().get("data", [])

    rows = cached(("calendar", kind, tuple(markets), ts_from, ts_to, limit), produce, ttl=300)
    return [{"symbol": row["s"], **dict(zip(spec["columns"], row["d"]))} for row in rows]


# ─── Name -> symbol resolution ─────────────────────────────────────
_PREFERRED_TYPES = {"stock", "fund", "dr", "crypto", "spot", "etf"}
_MAIN_VENUES = {e for exchanges in MAIN_EXCHANGES.values() for e in exchanges} | {"BINANCE", "COINBASE"}


# Everyday names for the biggest coins, so "bitcoin" or "btc" means the coin, not a company with that ticker.
CRYPTO_ALIASES = {
    "btc": "BTCUSDT", "bitcoin": "BTCUSDT", "eth": "ETHUSDT", "ethereum": "ETHUSDT", "sol": "SOLUSDT", "solana": "SOLUSDT",
    "xrp": "XRPUSDT", "ripple": "XRPUSDT", "bnb": "BNBUSDT", "doge": "DOGEUSDT", "dogecoin": "DOGEUSDT",
    "ada": "ADAUSDT", "cardano": "ADAUSDT", "matic": "MATICUSDT", "ltc": "LTCUSDT", "litecoin": "LTCUSDT",
    "dot": "DOTUSDT", "polkadot": "DOTUSDT", "shib": "SHIBUSDT", "avax": "AVAXUSDT", "link": "LINKUSDT", "trx": "TRXUSDT",
}


def resolve_symbol(query: str, prefer_country: str = "") -> dict:
    """Turn what a person typed ('reliance', 'apple', 'btc') into the best EXCHANGE:TICKER.

    prefer_country (for example "IN") breaks ties toward listings from that country, so
    "tcs" means India's TCS rather than an unrelated company with the same ticker."""
    q = query.strip()
    if q.lower() in CRYPTO_ALIASES:
        pair = CRYPTO_ALIASES[q.lower()]
        return {"query": query, "best": {"exchange": "BINANCE", "symbol": pair, "full_symbol": f"BINANCE:{pair}",
                                         "description": f"{q.upper()} / Tether (crypto)", "type": "spot"}, "alternatives": []}
    if ":" in q and SYMBOL_PATTERN.match(q.upper()):
        exchange, ticker = q.upper().split(":", 1)
        return {"query": query, "best": {"exchange": exchange, "symbol": ticker, "full_symbol": f"{exchange}:{ticker}"},
                "alternatives": []}
    results = search_symbols_raw(q)
    ql = q.lower()

    def score(r):
        s = 0
        s += 4 if (r["symbol"] or "").lower() == ql else 0
        s += 3 if (r["description"] or "").lower().startswith(ql) else 0
        s += 2 if r.get("primary") else 0
        s += 1 if r["exchange"] in _MAIN_VENUES else 0
        s += 2 if (r["type"] or "") in _PREFERRED_TYPES else -2
        s += 3 if prefer_country and (r.get("country") or "").upper() == prefer_country.upper() else 0
        return s

    ranked = sorted(results, key=score, reverse=True)
    shaped = [{**r, "full_symbol": f"{r['exchange']}:{r['symbol']}"} for r in ranked]
    return {"query": query, "best": shaped[0] if shaped else None, "alternatives": shaped[1:5]}
