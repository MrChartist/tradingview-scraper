"""Choosing which provider answers.

Order matters: PROVIDERS=zerodha,tradingview asks Zerodha first and uses TradingView only for what
Zerodha cannot answer (or for symbols it does not have). Leave a provider out to turn it off.
"""
import logging
from typing import Callable, Dict, List, Optional, Tuple

from api.config import Settings
from api.errors import ApiError
from api.providers.base import NotSupported, Provider, READ_CAPABILITIES, WRITE_CAPABILITIES

logger = logging.getLogger("market_terminal.providers")

# name -> factory(settings) -> Provider. Plugins add to this with @provider_factory.
AVAILABLE: Dict[str, Callable[[Settings], Provider]] = {}


def provider_factory(name: str):
    """Decorator for plugin modules:  @provider_factory("zerodha")  def make(settings): return ZerodhaProvider(...)"""
    def register(factory: Callable[[Settings], Provider]):
        if name in AVAILABLE:
            raise ValueError(f"A provider called '{name}' is already available")
        AVAILABLE[name] = factory
        return factory
    return register


def _exchange_of(symbol: str) -> str:
    return symbol.split(":", 1)[0].upper()


class Providers:
    def __init__(self) -> None:
        self._items: List[Provider] = []

    def clear(self) -> None:
        self._items = []

    def add(self, provider: Provider, allow_trading: bool = False) -> None:
        unknown = provider.capabilities - READ_CAPABILITIES - WRITE_CAPABILITIES
        if unknown:
            raise ValueError(f"Provider '{provider.name}' declares unknown capabilities: {sorted(unknown)}")
        writes = provider.capabilities & WRITE_CAPABILITIES
        if writes and not allow_trading:
            raise ValueError(f"Provider '{provider.name}' offers {sorted(writes)}, but Tickvale is read-only. "
                             "Trading is not enabled (see docs/PROVIDERS.md).")
        self._items.append(provider)

    @property
    def enabled(self) -> List[Provider]:
        return list(self._items)

    def describe(self) -> List[dict]:
        out = []
        for p in self._items:
            try:
                out.append(p.info())
            except Exception as e:           # a sick provider must not break /status
                out.append({"name": p.name, "capabilities": sorted(p.capabilities), "ok": False, "error": str(e)})
        return out

    def _candidates(self, capability: str, exchange: Optional[str]) -> List[Provider]:
        found = [p for p in self._items if capability in p.capabilities and p.serves(exchange)]
        if not found:
            names = ", ".join(p.name for p in self._items) or "none"
            raise ApiError(501, f"No enabled data source offers '{capability}'" + (f" for {exchange}" if exchange else "") + ".",
                           code="not_available", hint=f"Enabled sources: {names}. Change them with the PROVIDERS setting.")
        return found

    def call(self, capability: str, *args, exchange: Optional[str] = None, **kwargs) -> Tuple[str, object]:
        """Ask providers in order. Returns (provider_name, answer). A provider that cannot answer, or
        fails, hands over to the next; if all do, the last real error is raised."""
        last: Optional[Exception] = None
        for p in self._candidates(capability, exchange):
            try:
                return p.name, getattr(p, capability)(*args, **kwargs)
            except NotSupported:
                continue
            except Exception as e:
                logger.info("%s could not answer %s: %s", p.name, capability, e)
                last = e
        if last is not None:
            raise last
        raise ApiError(501, f"No enabled data source could answer '{capability}'.", code="not_available")

    def quotes(self, symbols: List[str]) -> Tuple[List[dict], List[str], List[str]]:
        """Each provider fills in what it can; the rest goes to the next. Returns (found, missing, sources)."""
        remaining, found, used = list(symbols), {}, []
        errors: List[Exception] = []
        any_candidate = False
        for p in self._items:
            if "quotes" not in p.capabilities:
                continue
            mine = [s for s in remaining if p.serves(_exchange_of(s))]
            if not mine:
                continue
            any_candidate = True
            try:
                got, _missing = p.quotes(mine)
            except NotSupported:
                continue
            except Exception as e:
                logger.info("%s could not answer quotes: %s", p.name, e)
                errors.append(e)
                continue
            for q in got:
                found[q["symbol"]] = q
            if got:
                used.append(p.name)
            remaining = [s for s in remaining if s not in found]
            if not remaining:
                break
        if not any_candidate:
            exchanges = sorted({_exchange_of(s) for s in symbols})
            names = ", ".join(p.name for p in self._items) or "none"
            raise ApiError(501, f"No enabled data source serves {', '.join(exchanges)}.", code="not_available",
                           hint=f"Enabled sources: {names}. Change them with the PROVIDERS setting.")
        if not found and errors:
            raise errors[-1]
        return [found[s] for s in symbols if s in found], remaining, used


registry = Providers()


def build(settings: Settings) -> Providers:
    """(Re)create the enabled providers from settings, in the order given."""
    from api.providers.tradingview import TradingViewProvider
    AVAILABLE.setdefault("tradingview", lambda s: TradingViewProvider())
    registry.clear()
    for name in settings.providers or ["tradingview"]:
        factory = AVAILABLE.get(name)
        if factory is None:
            raise RuntimeError(f"Unknown provider '{name}' in PROVIDERS. Available: {', '.join(sorted(AVAILABLE))}. "
                               "Providers from plugins are available after the plugin is listed in PLUGINS.")
        registry.add(factory(settings), allow_trading=settings.allow_trading)
        logger.info("Data source enabled: %s", name)
    return registry
