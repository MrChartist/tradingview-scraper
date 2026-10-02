"""Operations: every capability, written once.

REST (`api/v1.py`) and the WebSocket (`api/socket.py`) both call `execute()`, so a
capability behaves identically on either transport. To add a capability, write a
function and decorate it with `@operation(...)`; it is then reachable as
`op: "<name>"` on the socket (see docs/EXTENDING.md). Handlers are plain blocking
functions: transports run them in a worker thread.
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Literal, Optional, Type, Union

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from typing_extensions import Annotated

from api import services as svc
from api.config import Settings
from api.errors import ApiError
from api.glossary import CATALOG, FIELD_TERMS, GLOSSARY
from api.live import valid_symbol


@dataclass
class Result:
    data: Any
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Context:
    """What an operation may use. `auto_resolve` lets callers pass plain names like "reliance"."""
    settings: Settings
    hub: Any = None
    auto_resolve: bool = False


@dataclass(frozen=True)
class Operation:
    name: str
    summary: str
    model: Type[BaseModel]
    fn: Callable[[BaseModel, Context], Result]
    public: bool = False          # no API key needed


REGISTRY: Dict[str, Operation] = {}


def operation(name: str, summary: str, model: Type[BaseModel], public: bool = False):
    def register(fn: Callable[[BaseModel, Context], Result]):
        if name in REGISTRY:
            raise ValueError(f"Operation '{name}' is already registered")
        REGISTRY[name] = Operation(name, summary, model, fn, public)
        return fn
    return register


def execute(name: str, params: Optional[dict], ctx: Context) -> Result:
    """Validate params and run one operation. Raises ApiError for anything the caller can fix."""
    op = REGISTRY.get(name)
    if op is None:
        raise ApiError(400, f"Unknown operation '{name}'.", code="unknown_operation",
                       hint="Available operations: " + ", ".join(sorted(REGISTRY)) + ".")
    try:
        parsed = op.model.model_validate(params or {})
    except ValidationError as e:
        first = e.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", []))
        raise ApiError(422, f"{where}: {first.get('msg', 'invalid value')}".strip(": "),
                       code="validation_error", hint=f"Parameters for '{name}': {', '.join(op.model.model_fields) or 'none'}.")
    try:
        return op.fn(parsed, ctx)
    except svc.HTTPException as e:       # the data layer speaks HTTP; callers here speak ApiError
        raise ApiError(e.status_code, e.detail if isinstance(e.detail, str) else "Request failed")


def describe() -> List[dict]:
    return [{"name": o.name, "summary": o.summary, "public": o.public, "params": list(o.model.model_fields)}
            for o in sorted(REGISTRY.values(), key=lambda o: o.name)]


# ── shared parsing ─────────────────────────────────────────────────
def as_list(value: Union[str, List[str], None]) -> List[str]:
    if value is None:
        return []
    items = value.split(",") if isinstance(value, str) else value
    return [str(s).strip() for s in items if str(s).strip()]


def resolve_name(text: str, ctx: Context) -> str:
    """'reliance' -> 'NSE:RELIANCE' when the caller allows it; qualified symbols pass through."""
    text = text.strip()
    if ":" in text:
        return text.upper()
    if not ctx.auto_resolve:
        raise ApiError(400, f"Invalid symbol format: {text.upper()}. Use EXCHANGE:TICKER, for example NSE:RELIANCE.",
                       hint=f"Don't know the code? GET /v1/symbols/resolve?q={text.lower().replace(' ', '%20')} finds the best match.")
    try:
        result = svc.resolve_symbol(text, ctx.settings.default_country)
    except Exception:
        raise ApiError(502, "Symbol search is unavailable right now.")
    if not result["best"]:
        raise ApiError(404, f"Nothing matched '{text}'.", hint="Try a shorter or different spelling, for example the company's short name.")
    return result["best"]["full_symbol"]


def symbol_list(raw, ctx: Context, limit: int) -> List[str]:
    items = as_list(raw)
    if not items:
        raise ApiError(400, "Pass at least one symbol, for example symbols=NSE:RELIANCE,NASDAQ:AAPL.",
                       hint="Don't know the code? Use the 'resolve' operation (GET /v1/symbols/resolve?q=reliance).")
    if len(items) > limit:
        raise ApiError(400, f"At most {limit} symbols per request.", hint="Split the list into several requests.")
    plain = [s for s in items if ":" not in s]
    if len(plain) > 1 and ctx.auto_resolve:      # look names up side by side, not one after another
        with ThreadPoolExecutor(max_workers=min(len(plain), 8)) as pool:
            resolved = dict(zip(plain, pool.map(lambda s: resolve_name(s, ctx), plain)))
    else:
        resolved = {}
    symbols = list(dict.fromkeys(resolved.get(s) or resolve_name(s, ctx) for s in items))
    bad = [s for s in symbols if not valid_symbol(s)]
    if bad:
        raise ApiError(400, f"Invalid symbol format: {', '.join(bad[:5])}. Use EXCHANGE:TICKER, for example NSE:RELIANCE.",
                       hint="Don't know the code? GET /v1/symbols/resolve?q=" + bad[0].lower().replace(" ", "%20") + " finds the best match.")
    return symbols


def split_symbol(raw: str, ctx: Context):
    exchange, ticker = resolve_name(raw, ctx).split(":", 1)
    return exchange, ticker


def parse_when(value: Optional[str], default: int) -> int:
    """Accept epoch seconds or an ISO date (YYYY-MM-DD)."""
    if value is None or value == "":
        return default
    try:
        text = str(value)
        if text.isdigit():
            return int(text)
        return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        raise ApiError(400, f"Invalid date '{value}'. Use YYYY-MM-DD or epoch seconds.", hint="Example: from=2026-10-01&to=2026-10-14")


# ── parameter models ───────────────────────────────────────────────
class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


Params = _Params      # base class for plugin parameter models


class NoParams(_Params):
    pass


class SearchParams(_Params):
    q: str = Field(..., min_length=1, max_length=40, description="Part of a name or ticker")
    limit: int = Field(10, ge=1, le=30)


class ResolveParams(_Params):
    q: str = Field(..., min_length=1, max_length=60, description="A company, ticker or coin name")
    country: Optional[str] = Field(None, min_length=2, max_length=2, description="Two-letter country to prefer on ties, e.g. IN")


class QuotesParams(_Params):
    symbols: Union[str, List[str]] = Field(..., description="List or comma separated, e.g. ['NSE:RELIANCE', 'bitcoin']")


class SymbolParams(_Params):
    symbol: str = Field(..., min_length=1, max_length=60, description="EXCHANGE:TICKER (or, on the socket, a plain name)")


class TechnicalsParams(SymbolParams):
    timeframe: str = "1d"


class CandlesParams(SymbolParams):
    timeframe: str = "1d"
    limit: int = Field(100, ge=1, le=5000)


class NewsParams(SymbolParams):
    limit: int = Field(20, ge=1, le=100)
    language: str = Field("en", min_length=2, max_length=5)


class MoversParams(_Params):
    market: str = "stocks-india"
    category: str = "gainers"
    limit: int = Field(25, ge=1, le=100)


OPERATORS = {"gt": "greater", "gte": "egreater", "lt": "less", "lte": "eless",
             "eq": "equal", "neq": "nequal", "in": "in_range", "between": "in_range"}
FIELD_PATTERN = r"^[A-Za-z0-9_.]{1,60}$"


class Condition(_Params):
    field: str = Field(..., pattern=FIELD_PATTERN, examples=["market_cap_basic"])
    op: Literal["gt", "gte", "lt", "lte", "eq", "neq", "in", "between"]
    value: Any = Field(..., description="A number or string; a list for 'in' and [low, high] for 'between'.")


class ScreenerParams(_Params):
    market: str = Field("india", examples=["india", "america", "crypto"])
    conditions: List[Condition] = Field(default_factory=list, max_length=20)
    columns: Optional[List[Annotated[str, StringConstraints(pattern=FIELD_PATTERN)]]] = Field(
        None, max_length=30, description="Fields to return. Defaults to a standard set.")
    sort_by: str = Field("volume", pattern=FIELD_PATTERN)
    sort_order: Literal["asc", "desc"] = "desc"
    limit: int = Field(25, ge=1, le=200)
    main_only: bool = Field(True, description="Restrict to the market's main exchange (for example NSE for India).")


class CalendarParams(_Params):
    markets: Union[str, List[str]] = Field("india", description="india, america, uk, ...")
    from_: Optional[str] = Field(None, alias="from", description="YYYY-MM-DD or epoch seconds. Default: today.")
    to: Optional[str] = Field(None, description="YYYY-MM-DD or epoch seconds. Default: 7 days ahead.")
    limit: int = Field(100, ge=1, le=500)


# ── operations ─────────────────────────────────────────────────────
@operation("ping", "Check the connection. Returns the server time.", NoParams, public=True)
def op_ping(p, ctx):
    return Result({"time": int(time.time())})


@operation("status", "Service version, live-feed health and limits.", NoParams)
def op_status(p, ctx):
    s = ctx.settings
    return Result({
        "live": ctx.hub.stats() if ctx.hub else None,
        "auth": "api_key" if s.auth_enabled else "open",
        "rate_limit_per_minute": s.rate_limit_per_minute,
        "cache_entries": len(svc._CACHE),
    })


@operation("markets", "What you can ask for: markets, categories, timeframes, filter fields.", NoParams, public=True)
def op_markets(p, ctx):
    return Result(CATALOG)


@operation("glossary", "Plain-language meaning of every market term.", NoParams, public=True)
def op_glossary(p, ctx):
    return Result({"terms": GLOSSARY, "field_terms": FIELD_TERMS})


@operation("resolve", "Turn a name like 'reliance' or 'apple' into the right EXCHANGE:TICKER.", ResolveParams)
def op_resolve(p, ctx):
    try:
        result = svc.resolve_symbol(p.q, p.country or ctx.settings.default_country)
    except Exception:
        raise ApiError(502, "Symbol search is unavailable right now.")
    if not result["best"]:
        raise ApiError(404, f"Nothing matched '{p.q}'.", hint="Try a shorter or different spelling, for example the company's short name.")
    return Result(result, {"matched": 1 + len(result["alternatives"])})


@operation("search", "Find symbols by name or ticker.", SearchParams)
def op_search(p, ctx):
    try:
        results = svc.search_symbols_raw(p.q)[:p.limit]
    except Exception:
        raise ApiError(502, "Symbol search is unavailable right now.")
    return Result(results, {"count": len(results)})


@operation("quotes", "Latest quote for up to 100 symbols (snapshot).", QuotesParams)
def op_quotes(p, ctx):
    wanted = symbol_list(p.symbols, ctx, 100)
    try:
        found, missing = svc.snapshot_quotes(wanted)
    except Exception as e:
        svc.logger.warning("Snapshot failed: %s", e)
        raise ApiError(502, "Upstream market data is unavailable right now.")
    return Result(found, {"count": len(found), "not_found": missing})


@operation("symbol", "Profile and key statistics, in the listing currency.", SymbolParams)
def op_symbol(p, ctx):
    exchange, ticker = split_symbol(p.symbol, ctx)
    r = svc.run_scraper(lambda: svc.fetch_overview(exchange, ticker), "Symbol not found or data unavailable.")
    return Result(r["data"], {"currency": r["data"].get("currency")})


@operation("fundamentals", "Company fundamentals, in the listing currency.", SymbolParams)
def op_fundamentals(p, ctx):
    exchange, ticker = split_symbol(p.symbol, ctx)
    r = svc.run_scraper(lambda: svc.fetch_fundamentals(exchange, ticker), "Fundamental data not found.")
    return Result(r["data"], {"currency": r["data"].get("currency")})


@operation("technicals", "Technical indicator values (advanced).", TechnicalsParams)
def op_technicals(p, ctx):
    exchange, ticker = split_symbol(p.symbol, ctx)
    r = svc.run_scraper(lambda: svc.fetch_indicators(exchange, ticker, p.timeframe), "Indicators not found.")
    return Result(r["data"], {"timeframe": p.timeframe})


@operation("candles", "Historical OHLCV candles, oldest first (time = epoch seconds, UTC).", CandlesParams)
def op_candles(p, ctx):
    exchange, ticker = split_symbol(p.symbol, ctx)
    try:
        raw = svc.fetch_ohlcv(exchange, ticker, p.timeframe, max(p.limit, 5))
    except (ApiError, svc.HTTPException):
        raise
    except Exception as e:
        svc.logger.warning("Candles failed: %s", e)
        raise ApiError(502, "Could not fetch candles from upstream.")
    if not raw:
        raise ApiError(404, "No candles returned. Check the exchange, ticker and timeframe.")
    out = [{"time": int(c["timestamp"]),
            "datetime": datetime.fromtimestamp(c["timestamp"], timezone.utc).isoformat(),
            "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"],
            "volume": c.get("volume")} for c in raw[-p.limit:]]
    return Result(out, {"count": len(out), "timeframe": p.timeframe})


@operation("news", "Latest headlines for a symbol.", NewsParams)
def op_news(p, ctx):
    exchange, ticker = split_symbol(p.symbol, ctx)
    try:
        items = svc.fetch_news(exchange, ticker, p.limit, p.language)
    except Exception as e:
        svc.logger.warning("News failed: %s", e)
        raise ApiError(502, "News is unavailable right now.")
    return Result(items, {"count": len(items)})


@operation("movers", "Gainers, losers, most active (liquid, main-exchange listings only).", MoversParams)
def op_movers(p, ctx):
    r = svc.run_scraper(
        lambda: svc.cached(("movers", p.market, p.category, p.limit),
                           lambda: svc.movers_scraper.scrape(market=p.market, category=p.category, limit=p.limit)),
        "No data.")
    return Result(r["data"], {"count": len(r["data"]), "market": p.market, "category": p.category})


@operation("screener", "Screen a market with your own conditions.", ScreenerParams)
def op_screener(p, ctx):
    filters = [{"left": c.field, "operation": OPERATORS[c.op], "right": c.value} for c in p.conditions]
    if p.main_only and p.market in svc.MAIN_EXCHANGES:
        filters.append({"left": "exchange", "operation": "in_range", "right": svc.MAIN_EXCHANGES[p.market]})
    columns = p.columns or (svc.STOCK_SCREENER_COLUMNS if p.market in svc.STOCK_SCREENER_MARKETS else None)
    key = ("screener-v1", p.market, tuple(columns or ()), p.sort_by, p.sort_order, p.limit, json.dumps(filters, sort_keys=True, default=str))
    r = svc.run_scraper(
        lambda: svc.cached(key, lambda: svc.screener_scraper.screen(
            market=p.market, filters=filters or None, columns=columns,
            sort_by=p.sort_by, sort_order=p.sort_order, limit=p.limit)),
        "No data.")
    return Result(r["data"], {"count": len(r["data"]), "total_matches": r.get("totalCount"), "market": p.market})


def _calendar(kind: str, p: CalendarParams) -> Result:
    now = int(time.time())
    day = now - now % 86400
    start, end = parse_when(p.from_, day), parse_when(p.to, day + 7 * 86400)
    if end < start:
        raise ApiError(400, "'to' must not be before 'from'.")
    try:
        rows = svc.fetch_calendar(kind, as_list(p.markets), start, end, p.limit)
    except Exception as e:
        svc.logger.warning("Calendar failed: %s", e)
        raise ApiError(502, "Calendar data is unavailable right now.")
    return Result(rows, {"count": len(rows), "note": "Monetary values are in fundamental_currency_code (usually USD)."})


@operation("earnings", "Upcoming and recent earnings announcements.", CalendarParams)
def op_earnings(p, ctx):
    return _calendar("earnings", p)


@operation("dividends", "Upcoming and recent dividends.", CalendarParams)
def op_dividends(p, ctx):
    return _calendar("dividends", p)
