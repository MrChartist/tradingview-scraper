"""What a data source must look like.

A provider answers some of the questions Tickvale can be asked. It lists what it can do in
`capabilities`; anything it does not list is skipped and the next provider is tried. Brokers,
exchanges and the TradingView reader all implement this same small interface.

Read-only by design: the reserved WRITE capabilities (placing or changing orders) are refused at
registration unless trading is explicitly allowed, and no operation uses them yet.
"""
from typing import Dict, List, Optional, Set, Tuple

READ_CAPABILITIES = {
    "search", "quotes", "candles", "overview", "fundamentals", "technicals",
    "news", "movers", "screener", "calendar", "corporate_actions", "market_breadth",
}
# Reserved for later. Nothing in Tickvale calls these today.
WRITE_CAPABILITIES = {"place_order", "modify_order", "cancel_order"}


class NotSupported(Exception):
    """Raised by a provider for a question it cannot answer; the next provider is tried."""


class Provider:
    name: str = ""
    capabilities: Set[str] = set()
    # Exchanges this provider serves (e.g. {"NSE", "BSE"}). None means any exchange.
    exchanges: Optional[Set[str]] = None
    # Free text shown in /status so people know what is behind an answer.
    description: str = ""

    def serves(self, exchange: Optional[str]) -> bool:
        return self.exchanges is None or exchange is None or exchange.upper() in self.exchanges

    def health(self) -> Dict:
        """Cheap self-check for /status. Must not raise or hit the network heavily."""
        return {"ok": True}

    def info(self) -> Dict:
        return {"name": self.name, "description": self.description, "capabilities": sorted(self.capabilities),
                "exchanges": sorted(self.exchanges) if self.exchanges else "any", **self.health()}

    # Each method below is optional. Implement the ones named in `capabilities`.
    def search(self, q: str) -> List[Dict]:
        raise NotSupported

    def quotes(self, symbols: List[str]) -> Tuple[List[Dict], List[str]]:
        """Return (quotes, symbols_not_found). Quote shape: see docs/WEBSOCKET.md."""
        raise NotSupported

    def candles(self, exchange: str, ticker: str, timeframe: str, limit: int) -> List[Dict]:
        """Oldest first: [{timestamp, open, high, low, close, volume}]. timestamp = epoch seconds."""
        raise NotSupported

    def overview(self, exchange: str, ticker: str) -> Dict:
        """{"status": "success", "data": {...}} or {"status": "failed", "error": "..."}."""
        raise NotSupported

    def fundamentals(self, exchange: str, ticker: str) -> Dict:
        raise NotSupported

    def technicals(self, exchange: str, ticker: str, timeframe: str) -> Dict:
        raise NotSupported

    def news(self, exchange: str, ticker: str, limit: int, language: str) -> List[Dict]:
        raise NotSupported

    def movers(self, market: str, category: str, limit: int) -> Dict:
        raise NotSupported

    def screener(self, market: str, filters: List[Dict], columns: Optional[List[str]],
                 sort_by: str, sort_order: str, limit: int) -> Dict:
        raise NotSupported

    def calendar(self, kind: str, markets: List[str], ts_from: int, ts_to: int, limit: int) -> List[Dict]:
        raise NotSupported

    def corporate_actions(self, exchange: str, ticker: str, date_from: str, date_to: str) -> List[Dict]:
        """Dividends, splits, bonus issues: [{ex_date, type, purpose, adjustment_factor}]. Dates are YYYY-MM-DD."""
        raise NotSupported

    def market_breadth(self, exchange: str, date: Optional[str]) -> Dict:
        """How many stocks rose, fell or were unchanged on a day: {date, advances, declines, unchanged, ...}."""
        raise NotSupported
