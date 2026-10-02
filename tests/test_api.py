"""Offline tests for the FastAPI layer and the market-movers fixes (scrapers are mocked)."""
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from api import main
from tradingview_scraper.symbols.market_movers import MarketMovers


@pytest.fixture(autouse=True)
def clear_cache():
    main._CACHE.clear()
    yield
    main._CACHE.clear()


@pytest.fixture
def client():
    return TestClient(main.app)


ROWS = [{"symbol": "NSE:ABC", "name": "ABC", "close": 10.5, "change": 2.5, "volume": 1000}]


def test_frontend_is_served(client):
    assert "Open Market" in client.get("/").text and "Chartist" not in client.get("/").text
    assert client.get("/style.css").status_code == 200
    assert client.get("/script.js").status_code == 200


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_movers_ok_and_cached(client):
    with mock.patch.object(main.movers_scraper, "scrape", return_value={"status": "success", "data": ROWS}) as m:
        assert client.get("/api/movers?market=stocks-india").json()["data"] == ROWS
        client.get("/api/movers?market=stocks-india")
        assert m.call_count == 1  # second call served from cache


def test_movers_bad_category_is_400(client):
    r = client.get("/api/movers?market=crypto&category=penny-stocks")
    assert r.status_code == 400


def test_movers_limit_validated(client):
    assert client.get("/api/movers?limit=0").status_code == 422
    assert client.get("/api/movers?limit=1000").status_code == 422


def test_invalid_timeframe_is_400(client):
    assert client.get("/api/ohlcv/NSE/TCS?timeframe=2x").status_code == 400
    assert client.get("/api/indicators/NSE/TCS?timeframe=bad").status_code == 400


def test_not_found_maps_to_404(client):
    with mock.patch.object(main.overview_scraper, "get_symbol_overview", return_value={"status": "failed"}):
        assert client.get("/api/overview/NSE/NOPE").status_code == 404


def test_screener_builds_filters(client):
    with mock.patch.object(main.screener_scraper, "screen", return_value={"status": "success", "data": ROWS}) as m:
        client.get("/api/screener?market=india&min_price=5&max_change=-2&min_market_cap=1e9&main_only=false")
    filters = m.call_args.kwargs["filters"]
    assert {"left": "close", "operation": "egreater", "right": 5.0} in filters
    assert {"left": "change", "operation": "eless", "right": -2.0} in filters
    assert len(filters) == 3


def test_download_screener_respects_filters(client):
    with mock.patch.object(main.screener_scraper, "screen", return_value={"status": "success", "data": ROWS}) as m:
        r = client.get("/api/download/screener?market=india&min_price=5&main_only=false&fmt=csv")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert m.call_args.kwargs["filters"] == [{"left": "close", "operation": "egreater", "right": 5.0}]


def test_download_ohlcv_json(client):
    candles = [{"index": 0, "timestamp": 1, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 9}]
    with mock.patch.object(main, "fetch_ohlcv", return_value=candles):
        r = client.get("/api/download/ohlcv/NSE/TCS?fmt=json")
    assert r.json() == candles


def test_csv_formula_injection_is_neutralised():
    assert main._csv_safe("=1+1") == "'=1+1"
    assert main._csv_safe("-5.5") == "-5.5"
    assert main._csv_safe({"a": 1}) == '{"a": 1}'


def test_search_maps_results(client):
    fake = [{"symbol": "RELIANCE", "exchange": "NSE", "description": "Reliance", "type": "stock", "country": "IN", "currency_code": "INR"}]
    with mock.patch.object(main.requests, "get") as g:
        g.return_value.json.return_value = {"symbols": fake}
        g.return_value.raise_for_status.return_value = None
        data = client.get("/api/search?q=reliance").json()["data"]
    assert data[0]["exchange"] == "NSE" and data[0]["symbol"] == "RELIANCE"


# ─── MarketMovers fixes ────────────────────────────────────────────

@pytest.mark.parametrize("market,region", [
    ("stocks-india", "india"), ("stocks-uk", "uk"), ("stocks-usa", "america"),
    ("stocks-canada", "canada"), ("crypto", "crypto"),
])
def test_scanner_url_matches_market(market, region):
    assert MarketMovers()._get_scanner_url(market).endswith(f"/{region}/scan")


def test_main_exchange_and_liquidity_filters_applied():
    f = MarketMovers()._get_filter_conditions("stocks-india", "gainers")
    assert {"left": "exchange", "operation": "in_range", "right": ["NSE"]} in f
    assert any(x["left"] == "volume" for x in f)


def test_premarket_uses_premarket_columns():
    mm = MarketMovers()
    payload = mm._build_scanner_payload("stocks-usa", "pre-market-gainers", limit=5)
    assert "premarket_change" in payload["columns"]
    assert payload["sort"]["sortBy"] == "premarket_change"


def test_extended_hours_rejected_outside_usa():
    with pytest.raises(ValueError):
        MarketMovers()._validate_category("pre-market-gainers", "stocks-india")


def test_screener_defaults_to_main_exchange(client):
    with mock.patch.object(main.screener_scraper, "screen", return_value={"status": "success", "data": ROWS}) as m:
        client.get("/api/screener?market=india")
    assert {"left": "exchange", "operation": "in_range", "right": ["NSE"]} in m.call_args.kwargs["filters"]


def test_native_currency_replaces_usd_figures():
    resp = {"status": "success", "data": {"market_cap_basic": 163.8e9, "debt_to_equity": 0.4, "close": 1167.7}}
    native = {"market_cap_basic": 15.78e12, "currency": "INR", "fundamental_currency_code": "INR"}
    with mock.patch.object(main.requests, "post") as post:
        post.return_value.json.return_value = {"data": [{"d": [native["market_cap_basic"], "INR", "INR"]}]}
        post.return_value.raise_for_status.return_value = None
        out = main.with_native_currency("NSE", "RELIANCE", resp)["data"]
    assert out["market_cap_basic"] == 15.78e12 and out["currency"] == "INR"
    assert out["debt_to_equity"] == 0.4          # ratios untouched
    assert resp["data"]["market_cap_basic"] == 163.8e9   # input not mutated


def test_unknown_exchange_keeps_usd():
    out = main.with_native_currency("BINANCE", "BTCUSDT", {"status": "success", "data": {"market_cap_basic": 5}})["data"]
    assert out["currency"] == "USD" and out["market_cap_basic"] == 5


def test_money_field_rule():
    assert main.is_money_field("market_cap_basic") and main.is_money_field("total_revenue")
    assert main.is_money_field("earnings_per_share_basic_ttm")
    assert not main.is_money_field("gross_margin") and not main.is_money_field("return_on_assets_fq")
    assert not main.is_money_field("debt_to_equity") and not main.is_money_field("price_earnings_ttm")
