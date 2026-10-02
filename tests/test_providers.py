"""Data-source layer: TradingView is one optional provider; others slot in with fallback; read-only guard."""
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from api import providers
from api import services as svc
from api.config import Settings
from api.main import create_app
from api.providers import NotSupported, Provider
from tests.test_v1 import H

TV_QUOTE = {"symbol": "BINANCE:BTCUSDT", "status": "ok", "price": 1.0}
NSE_QUOTE = {"symbol": "NSE:TCS", "status": "ok", "price": 2.0, "source_note": "broker"}


class FakeBroker(Provider):
    name = "fakebroker"
    description = "Test broker"
    capabilities = {"quotes", "candles"}
    exchanges = {"NSE", "BSE"}

    def quotes(self, symbols):
        return [dict(NSE_QUOTE, symbol=s) for s in symbols if s != "NSE:MISSING"], [s for s in symbols if s == "NSE:MISSING"]

    def candles(self, exchange, ticker, timeframe, limit):
        return [{"timestamp": 1700000000, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 9}]


class BrokenBroker(FakeBroker):
    name = "brokenbroker"

    def quotes(self, symbols):
        raise RuntimeError("session expired")

    def candles(self, *a):
        raise NotSupported


@pytest.fixture
def make_app():
    added = []

    def make(*names, **settings):
        for factory_name, cls in (("fakebroker", FakeBroker), ("brokenbroker", BrokenBroker)):
            if factory_name not in providers.AVAILABLE:
                providers.AVAILABLE[factory_name] = lambda s, cls=cls: cls()
                added.append(factory_name)
        return TestClient(create_app(Settings(api_keys={"k-secret": "acme"}, enable_web_ui=False,
                                              providers=list(names) or ["tradingview"], **settings)))
    yield make
    for name in added:
        providers.AVAILABLE.pop(name, None)
    providers.build(Settings())               # leave the global registry as the default


def test_default_is_tradingview_only(make_app):
    with make_app() as c:
        sources = c.get("/v1/status", headers=H).json()["data"]["sources"]
    assert [s["name"] for s in sources] == ["tradingview"] and "quotes" in sources[0]["capabilities"]


def test_broker_first_then_tradingview_fills_gaps(make_app):
    with make_app("fakebroker", "tradingview") as c, mock.patch.object(svc, "snapshot_quotes", return_value=([TV_QUOTE], [])):
        body = c.get("/v1/quotes?symbols=NSE:TCS,BINANCE:BTCUSDT", headers=H).json()
    assert [q["symbol"] for q in body["data"]] == ["NSE:TCS", "BINANCE:BTCUSDT"]       # input order kept
    assert body["data"][0]["source_note"] == "broker" and body["meta"]["sources"] == ["fakebroker", "tradingview"]


def test_unknown_to_the_broker_falls_through_to_tradingview(make_app):
    with make_app("fakebroker", "tradingview") as c, \
            mock.patch.object(svc, "snapshot_quotes", return_value=([dict(TV_QUOTE, symbol="NSE:MISSING")], [])):
        body = c.get("/v1/quotes?symbols=NSE:MISSING", headers=H).json()
    assert body["data"][0]["symbol"] == "NSE:MISSING" and body["meta"]["sources"] == ["tradingview"]


def test_a_failing_provider_hands_over_instead_of_failing_the_request(make_app):
    with make_app("brokenbroker", "tradingview") as c, mock.patch.object(svc, "snapshot_quotes", return_value=([TV_QUOTE], [])):
        body = c.get("/v1/quotes?symbols=BINANCE:BTCUSDT,NSE:TCS", headers=H).json()
    assert body["meta"]["sources"] == ["tradingview"] and body["meta"]["count"] == 1


def test_provider_that_cannot_answer_a_capability_is_skipped(make_app):
    rows = {"status": "success", "data": [{"symbol": "NSE:ABC"}]}
    with make_app("fakebroker", "tradingview") as c, mock.patch.object(svc.movers_scraper, "scrape", return_value=rows):
        movers = c.get("/v1/markets/movers", headers=H).json()
        candles = c.get("/v1/symbols/NSE/TCS/candles", headers=H).json()
    assert movers["meta"]["source"] == "tradingview"            # the broker has no movers
    assert candles["meta"]["source"] == "fakebroker"            # but it does have candles


def test_tradingview_can_be_switched_off(make_app):
    with make_app("fakebroker") as c:
        r = c.get("/v1/markets/movers", headers=H)
        quotes = c.get("/v1/quotes?symbols=NSE:TCS", headers=H).json()
        outside = c.get("/v1/quotes?symbols=BINANCE:BTCUSDT", headers=H)
    assert r.status_code == 501 and r.json()["error"]["code"] == "not_available"
    assert "PROVIDERS" in r.json()["error"]["hint"] and "fakebroker" in r.json()["error"]["hint"]
    assert quotes["data"][0]["symbol"] == "NSE:TCS" and quotes["meta"]["sources"] == ["fakebroker"]
    assert outside.status_code == 501                           # nobody serves Binance


def test_unknown_provider_name_stops_startup_with_the_choices():
    with pytest.raises(RuntimeError) as e:
        create_app(Settings(providers=["nope"], enable_web_ui=False))
    assert "nope" in str(e.value) and "tradingview" in str(e.value)
    providers.build(Settings())


def test_read_only_guard_refuses_order_capabilities():
    class Trader(Provider):
        name = "trader"
        capabilities = {"quotes", "place_order"}

    reg = providers.registry.__class__()
    with pytest.raises(ValueError) as e:
        reg.add(Trader())
    assert "read-only" in str(e.value)
    reg.add(Trader(), allow_trading=True)                       # only when explicitly allowed
    with pytest.raises(ValueError):
        reg.add(type("Bad", (Provider,), {"name": "bad", "capabilities": {"teleport"}})())


def test_hello_lists_the_sources():
    with TestClient(create_app(Settings(api_keys={"k-secret": "acme"}, enable_web_ui=False))) as c, \
            c.websocket_connect("/v1/ws", headers=H) as ws:
        hello = ws.receive_json()
    assert hello["sources"][0]["name"] == "tradingview"
