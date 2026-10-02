"""The TradingView reader, as one provider among others.

Public web endpoints, unofficial, and subject to TradingView's terms. It is optional: leave it out of
PROVIDERS to switch it off, or list it after your brokers so it only fills gaps.
"""
import json
from typing import Dict, List, Tuple

from api import services as svc
from api.providers.base import Provider, READ_CAPABILITIES


class TradingViewProvider(Provider):
    name = "tradingview"
    description = "TradingView public endpoints (unofficial). Stock prices are usually 15 minutes delayed."
    capabilities = set(READ_CAPABILITIES) - {"corporate_actions", "market_breadth"}

    # Looked up on `svc` at call time so tests and plugins can swap the underlying functions.
    def search(self, q: str) -> List[Dict]:
        return svc.search_symbols_raw(q)

    def quotes(self, symbols: List[str]) -> Tuple[List[Dict], List[str]]:
        return svc.snapshot_quotes(symbols)

    def candles(self, exchange, ticker, timeframe, limit):
        return svc.fetch_ohlcv(exchange, ticker, timeframe, limit)

    def overview(self, exchange, ticker):
        return svc.fetch_overview(exchange, ticker)

    def fundamentals(self, exchange, ticker):
        return svc.fetch_fundamentals(exchange, ticker)

    def technicals(self, exchange, ticker, timeframe):
        return svc.fetch_indicators(exchange, ticker, timeframe)

    def news(self, exchange, ticker, limit, language):
        return svc.fetch_news(exchange, ticker, limit, language)

    def movers(self, market, category, limit):
        return svc.cached(("movers", market, category, limit),
                          lambda: svc.movers_scraper.scrape(market=market, category=category, limit=limit))

    def screener(self, market, filters, columns, sort_by, sort_order, limit):
        key = ("screener-v1", market, tuple(columns or ()), sort_by, sort_order, limit,
               json.dumps(filters, sort_keys=True, default=str))
        return svc.cached(key, lambda: svc.screener_scraper.screen(
            market=market, filters=filters or None, columns=columns,
            sort_by=sort_by, sort_order=sort_order, limit=limit))

    def calendar(self, kind, markets, ts_from, ts_to, limit):
        return svc.fetch_calendar(kind, markets, ts_from, ts_to, limit)
