"""NSE India's official MCP servers, as a data source.

Two public servers (no key, no login):
  live  https://mcp.nseindia.in/cmmkt/mcp         snapshot of the current trading day, refreshed about every 5 minutes
  eod   https://mcp.nseindia.in/bhavcopy/cm/mcp   end-of-day (Bhavcopy) data for roughly the last 5 years

Terms: NSE describes this data as for informational and educational use, and not for real-time trading,
commercial deployment or training AI models. Read docs/PROVIDERS.md before relying on it. Daily candles only;
intraday history, indices, fundamentals, news and screening stay with the other providers.
"""
import datetime
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

import requests

from api import markets_india as india
from api import services as svc
from api.config import Settings
from api.providers.base import NotSupported, Provider
from api.providers.mcp import McpClient, McpError

logger = logging.getLogger("market_terminal.nse")

LIVE_MAX_SYMBOLS = 25            # beyond this, one end-of-day bulk call is used instead of one live call per symbol
LIQUIDITY_FLOOR = 100_000        # same floor as the TradingView movers list, so the lists compare
REFRESH_SECONDS = 300


def nse_ticker(ticker: str) -> str:
    """TradingView writes BAJAJ_AUTO, NSE writes BAJAJ-AUTO."""
    return ticker.upper().replace("_", "-")


def tv_ticker(ticker: str) -> str:
    return ticker.upper().replace("-", "_")


def _not_found(payload) -> bool:
    return isinstance(payload, dict) and "error" in payload and "not found" in str(payload["error"]).lower()


class NseProvider(Provider):
    name = "nse"
    description = ("NSE India official MCP servers. Free, no key. Live prices about 5 minutes behind; daily history for ~5 years. "
                   "For informational and educational use.")
    capabilities = {"quotes", "candles", "movers", "corporate_actions", "market_breadth"}
    exchanges = {"NSE"}

    def __init__(self, settings: Optional[Settings] = None, live: Optional[McpClient] = None, eod: Optional[McpClient] = None):
        s = settings or Settings()
        self.live = live or McpClient(s.nse_live_url, timeout=20)
        self.eod = eod or McpClient(s.nse_eod_url, timeout=60)

    def health(self) -> Dict:
        return {"ok": True, "live": self.live.url, "eod": self.eod.url, "refresh_seconds": REFRESH_SECONDS}

    # ── quotes ────────────────────────────────────────────────────
    def quotes(self, symbols: List[str]) -> Tuple[List[Dict], List[str]]:
        if len(symbols) > LIVE_MAX_SYMBOLS:
            return self._bulk(symbols)
        with ThreadPoolExecutor(max_workers=min(8, len(symbols))) as pool:
            outcomes = list(pool.map(self._live_quote, symbols))
        errors = [o for o in outcomes if isinstance(o, Exception)]
        if errors and len(errors) == len(outcomes):
            raise errors[0]                        # nothing worked: let the next source try
        found = [o for o in outcomes if isinstance(o, dict)]
        missing = [s for s, o in zip(symbols, outcomes) if not isinstance(o, dict)]
        return found, missing

    def _live_quote(self, symbol: str):
        ticker = symbol.split(":", 1)[1]
        try:
            payload = svc.cached(("nse-quote", symbol), lambda: self.live.call("cm_get_stock_quote", {"symbol": nse_ticker(ticker)}), ttl=15)
        except (McpError, requests.RequestException) as e:
            return e
        if _not_found(payload):
            return None
        if not isinstance(payload, dict) or "stock" not in payload:
            return McpError(f"unexpected answer for {symbol}")
        return self._shape_live(symbol, payload["stock"])

    @staticmethod
    def _shape_live(symbol: str, st: Dict) -> Dict:
        as_of = india.parse_ist(st.get("latestTimestamp"))
        now = int(india.now_ist().timestamp())
        trading = india.is_trading_hours() and as_of is not None and now - as_of < 3600
        if trading:
            freshness = "about 5 min delayed"
        elif india.is_trading_hours():
            freshness = f"no new trades yet today (market may be closed). Last price {india.describe_time(as_of)}"
        else:
            freshness = f"market closed. Last price {india.describe_time(as_of)}"
        return {
            "symbol": symbol, "status": "ok",
            "price": st.get("lastTradedPrice"), "change": st.get("change"), "change_percent": st.get("perChange"),
            "open": st.get("openPrice"), "high": st.get("highPrice"), "low": st.get("lowPrice"),
            "prev_close": st.get("preClosePrice"), "volume": st.get("volume"), "bid": None, "ask": None,
            "currency": "INR", "name": st.get("symbol"), "description": st.get("symbol"), "exchange": "NSE", "type": "stock",
            "market_cap": None, "high_52w": st.get("fiftyTwoWeekHigh"), "low_52w": st.get("fiftyTwoWeekLow"),
            "sector": None, "industry": None,
            "update_mode": "nse_official_snapshot", "realtime": False, "delayed": True, "delay_seconds": REFRESH_SECONDS,
            "freshness": freshness, "market_open": trading, "session": "market" if trading else "out_of_session",
            "as_of": as_of,
        }

    def _bulk(self, symbols: List[str]) -> Tuple[List[Dict], List[str]]:
        by_ticker = {nse_ticker(s.split(":", 1)[1]): s for s in symbols}
        payload = self.eod.call("get_bulk_quote", {"symbols": list(by_ticker)}, timeout=60)
        found = []
        for q in (payload or {}).get("quotes", []):
            symbol = by_ticker.get(q.get("symbol"))
            if not symbol or q.get("close") is None:
                continue
            prev = q.get("prev_close")
            found.append({
                "symbol": symbol, "status": "ok", "price": q["close"],
                "change": round(q["close"] - prev, 2) if prev else None, "change_percent": q.get("pct_change"),
                "open": q.get("open"), "high": q.get("high"), "low": q.get("low"), "prev_close": prev, "volume": q.get("volume"),
                "bid": None, "ask": None, "currency": "INR", "name": q["symbol"], "description": q["symbol"],
                "exchange": "NSE", "type": "stock", "market_cap": None, "high_52w": None, "low_52w": None,
                "sector": None, "industry": None,
                "update_mode": "nse_official_eod", "realtime": False, "delayed": True, "delay_seconds": None,
                "freshness": f"end of day ({q.get('date')})", "market_open": False, "session": "out_of_session",
                "as_of": india.day_start_epoch(q.get("date")),
            })
        got = {q["symbol"] for q in found}
        return found, [s for s in symbols if s not in got]

    # ── candles (daily only) ──────────────────────────────────────
    def candles(self, exchange: str, ticker: str, timeframe: str, limit: int) -> List[Dict]:
        if timeframe != "1d":
            raise NotSupported
        months = max(1, min(math.ceil(limit / 20) + 1, 12))
        end = india.last_trading_day().isoformat()
        rows: Dict[int, Dict] = {}
        for _ in range(8):                                 # the server answers in chunks; walk back until we have enough
            payload = self.eod.call("get_stock_history", {"symbol": nse_ticker(ticker), "months": months, "endDate": end}, timeout=90)
            for r in (payload or {}).get("data", []):
                ts = india.day_start_epoch(r.get("date"))
                if ts is not None and r.get("close") is not None:
                    rows[ts] = {"timestamp": ts, "open": r.get("open"), "high": r.get("high"), "low": r.get("low"),
                                "close": r["close"], "volume": r.get("volume")}
            nxt = (payload or {}).get("next_end_date")
            if len(rows) >= limit or not nxt or nxt == end:
                break
            end = nxt
        if not rows:
            raise NotSupported                              # unknown to NSE's equity data (an index, say): let another source try
        return [rows[t] for t in sorted(rows)][-limit:]

    # ── movers (today, live) ──────────────────────────────────────
    def movers(self, market: str, category: str, limit: int) -> Dict:
        if market != "stocks-india" or category not in ("gainers", "losers"):
            raise NotSupported
        tool = "cm_get_live_gainers" if category == "gainers" else "cm_get_live_losers"
        payload = svc.cached(("nse-movers", category), lambda: self.live.call(tool), ttl=60)
        groups = (payload or {}).get("data") or {}
        rows: Dict[str, Dict] = {}
        for key, group in groups.items():
            if key == "legends":
                continue
            for it in (group.get("data") if isinstance(group, dict) else group) or []:
                volume = it.get("trade_quantity") or 0
                if it.get("series") != "EQ" or volume < LIQUIDITY_FLOOR or it.get("ltp") is None:
                    continue
                prev = it.get("prev_price")
                rows[it["symbol"]] = {
                    "symbol": f"NSE:{tv_ticker(it['symbol'])}", "name": it["symbol"], "description": it["symbol"],
                    "close": it["ltp"], "change": it.get("net_price"),
                    "change_abs": round(it["ltp"] - prev, 2) if prev else None, "volume": volume,
                    "market_cap_basic": None, "price_earnings_ttm": None, "currency": "INR",
                }
        ordered = sorted(rows.values(), key=lambda r: r["change"] or 0, reverse=(category == "gainers"))
        return {"status": "success", "data": ordered[:limit], "total": len(ordered)}

    # ── things only NSE's official data gives us ──────────────────
    def corporate_actions(self, exchange: str, ticker: str, date_from: str, date_to: str) -> List[Dict]:
        payload = self.eod.call("get_corporate_actions", {"symbol": nse_ticker(ticker), "fromDate": date_from, "toDate": date_to}, timeout=60)
        if isinstance(payload, dict) and "error" in payload:
            raise NotSupported
        return [{"ex_date": a.get("exDate"), "type": a.get("actionType"), "purpose": a.get("purpose"),
                 "adjustment_factor": a.get("adjustmentFactor")} for a in (payload or {}).get("actions", [])]

    def market_breadth(self, exchange: str, date: Optional[str]) -> Dict:
        if date:
            candidates = [date]
        else:                                    # no date given: the latest day that has data (skips weekends and holidays)
            day, candidates = india.last_trading_day(), []
            for _ in range(6):
                candidates.append(day.isoformat())
                day -= datetime.timedelta(days=1)
                while day.weekday() >= 5:
                    day -= datetime.timedelta(days=1)
        for asked in candidates:
            payload = self.eod.call("get_market_breadth", {"date": asked}, timeout=60)
            if isinstance(payload, dict) and payload.get("total_stocks"):
                return {"date": payload.get("date", asked), "stocks": payload["total_stocks"], "advances": payload.get("advances"),
                        "declines": payload.get("declines"), "unchanged": payload.get("unchanged"),
                        "advance_decline_ratio": payload.get("ad_ratio"), "total_volume": payload.get("total_volume")}
        raise NotSupported
