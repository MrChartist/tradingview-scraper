"""Offline tests for the product API (/v1): auth, rate limits, envelope, validation, live hub."""
import asyncio
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from api import services as svc
from api.config import Settings, parse_api_keys
from api.live import QuoteHub, Subscription, delay_info, normalize, valid_symbol
from api.main import create_app

KEY = {"k-secret": "acme"}


@pytest.fixture(autouse=True)
def clear_cache():
    svc._CACHE.clear()
    yield
    svc._CACHE.clear()


def make(**kw):
    defaults = dict(api_keys=KEY, rate_limit_per_minute=100, enable_web_ui=False)
    defaults.update(kw)
    return TestClient(create_app(Settings(**defaults)))


H = {"X-API-Key": "k-secret"}
QUOTE = {"symbol": "NSE:TCS", "status": "ok", "price": 1.0}


def test_parse_api_keys():
    assert parse_api_keys("acme:k1, k2") == {"k1": "acme", "k2": "client-2"}
    assert parse_api_keys("") == {}


def test_health_is_public_and_data_requires_key():
    with make() as c:
        assert c.get("/v1/health").json()["status"] == "ok"
        r = c.get("/v1/quotes?symbols=NSE:TCS")
        assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
        assert c.get("/v1/quotes?symbols=NSE:TCS", headers={"X-API-Key": "wrong"}).status_code == 401


def test_bearer_token_accepted_and_envelope():
    with make() as c, mock.patch.object(svc, "snapshot_quotes", return_value=([QUOTE], ["NSE:X"])):
        r = c.get("/v1/quotes?symbols=NSE:TCS,NSE:X", headers={"Authorization": "Bearer k-secret"})
    body = r.json()
    assert r.status_code == 200 and body["data"] == [QUOTE]
    assert body["meta"]["not_found"] == ["NSE:X"] and body["meta"]["request_id"]
    assert r.headers["x-ratelimit-limit"] == "100" and r.headers["x-request-id"]


def test_rate_limit_returns_429_with_retry_after():
    with make(rate_limit_per_minute=2) as c, mock.patch.object(svc, "snapshot_quotes", return_value=([], [])):
        codes = [c.get("/v1/quotes?symbols=NSE:TCS", headers=H).status_code for _ in range(3)]
        last = c.get("/v1/quotes?symbols=NSE:TCS", headers=H)
    assert codes == [200, 200, 429]
    assert last.json()["error"]["code"] == "rate_limited" and "retry-after" in last.headers


def test_open_mode_when_no_keys():
    with make(api_keys={}) as c, mock.patch.object(svc, "snapshot_quotes", return_value=([], [])):
        assert c.get("/v1/quotes?symbols=NSE:TCS").status_code == 200


def test_symbol_validation():
    with make() as c:
        for bad in ("reliance", "NSE:TCS,bad", ""):
            r = c.get(f"/v1/quotes?symbols={bad}", headers=H)
            assert r.status_code in (400, 422) and "error" in r.json()
        too_many = ",".join(f"NSE:S{i}" for i in range(101))
        assert c.get(f"/v1/quotes?symbols={too_many}", headers=H).status_code == 400


def test_validation_error_uses_error_envelope():
    with make() as c:
        r = c.get("/v1/markets/movers?limit=0", headers=H)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


def test_candles_shape_and_limit():
    raw = [{"index": i, "timestamp": 1700000000 + i * 86400, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10} for i in range(10)]
    with make() as c, mock.patch.object(svc, "fetch_ohlcv", return_value=raw):
        body = c.get("/v1/symbols/NSE/TCS/candles?limit=3", headers=H).json()
    assert [x["time"] for x in body["data"]] == [r["timestamp"] for r in raw[-3:]]
    assert body["data"][0]["datetime"].endswith("+00:00") and body["meta"]["count"] == 3


def test_candles_bad_timeframe():
    with make() as c:
        r = c.get("/v1/symbols/NSE/TCS/candles?timeframe=7x", headers=H)
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request"


def test_screener_post_maps_conditions():
    body = {"market": "india", "conditions": [{"field": "close", "op": "gte", "value": 10},
                                              {"field": "change", "op": "between", "value": [1, 5]}], "limit": 5}
    with make() as c, mock.patch.object(svc.screener_scraper, "screen", return_value={"status": "success", "data": [], "totalCount": 0}) as m:
        r = c.post("/v1/screener", json=body, headers=H)
    assert r.status_code == 200
    filters = m.call_args.kwargs["filters"]
    assert {"left": "close", "operation": "egreater", "right": 10} in filters
    assert {"left": "change", "operation": "in_range", "right": [1, 5]} in filters
    assert {"left": "exchange", "operation": "in_range", "right": ["NSE"]} in filters


def test_screener_rejects_bad_field_names():
    with make() as c:
        r = c.post("/v1/screener", json={"conditions": [{"field": "close; DROP", "op": "gt", "value": 1}]}, headers=H)
    assert r.status_code == 422


def test_calendar_date_parsing():
    with make() as c:
        assert c.get("/v1/calendar/earnings?from=garbage", headers=H).status_code == 400
        assert c.get("/v1/calendar/earnings?from=2026-10-10&to=2026-10-01", headers=H).status_code == 400


def test_cors_only_when_configured():
    with make(cors_origins=["https://app.example.com"]) as c:
        r = c.get("/v1/health", headers={"Origin": "https://app.example.com"})
        assert r.headers.get("access-control-allow-origin") == "https://app.example.com"
    with make() as c:
        assert "access-control-allow-origin" not in c.get("/v1/health", headers={"Origin": "https://evil.example"}).headers


def test_websocket_requires_key():
    with make() as c:
        with c.websocket_connect("/v1/ws?api_key=nope") as ws:
            assert ws.receive_json()["code"] == "unauthorized"


def test_websocket_subscribe_flow_rejects_bad_symbols():
    with make() as c, c.websocket_connect("/v1/ws", headers=H) as ws, \
            mock.patch.object(QuoteHub, "_ensure_running"):
        ws.send_json({"action": "subscribe", "symbols": ["nse:tcs", "bad symbol"]})
        msg = ws.receive_json()
        assert msg["type"] == "subscribed" and msg["symbols"] == ["NSE:TCS"]
        assert msg["rejected"] == [{"symbol": "BAD SYMBOL", "reason": "invalid_format"}]
        ws.send_json({"action": "ping"})
        assert ws.receive_json()["type"] == "pong"


# ── live hub (no network) ──────────────────────────────────────────
def test_symbol_format_and_delay_info():
    assert valid_symbol("NSE:M&M") and valid_symbol("NSE:BAJAJ-AUTO") and not valid_symbol("nse:tcs")
    assert delay_info("delayed_streaming_900") == {"realtime": False, "delayed": True, "delay_seconds": 900}
    assert delay_info("streaming")["realtime"] is True


def test_hub_merges_partial_updates_and_coalesces():
    async def run():
        hub = QuoteHub(max_symbols=2)
        a, b = Subscription("a"), Subscription("b")
        with mock.patch.object(QuoteHub, "_ensure_running"):
            await hub.subscribe(a, ["BINANCE:BTCUSDT"])
            await hub.subscribe(b, ["BINANCE:BTCUSDT"])
        hub._on_quote({"n": "BINANCE:BTCUSDT", "s": "ok", "v": {"lp": 100, "ch": 1}})
        hub._on_quote({"n": "BINANCE:BTCUSDT", "s": "ok", "v": {"lp": 101}})   # partial: ch must survive
        batch = await a.next_batch(timeout=1)
        assert len(batch) == 1 and batch[0]["price"] == 101 and batch[0]["change"] == 1   # coalesced, merged
        assert await b.next_batch(timeout=1)
        with pytest.raises(OverflowError):          # cap on tracked symbols
            with mock.patch.object(QuoteHub, "_ensure_running"):
                await hub.subscribe(a, ["NSE:A", "NSE:B"])
        await hub.release(a)
        await hub.release(b)
        assert hub.stats()["symbols"] == 0
    asyncio.run(run())


def test_hub_reports_unknown_symbol_and_late_joiner_gets_snapshot():
    async def run():
        hub = QuoteHub()
        a = Subscription("a")
        with mock.patch.object(QuoteHub, "_ensure_running"):
            await hub.subscribe(a, ["NSE:NOPE"])
            hub._on_quote({"n": "NSE:NOPE", "s": "error"})
            assert (await a.next_batch(timeout=1))[0]["status"] == "error"
            await hub.subscribe(a, ["NSE:TCS"])
            hub._on_quote({"n": "NSE:TCS", "s": "ok", "v": {"lp": 5}})
            late = Subscription("late")
            await hub.subscribe(late, ["NSE:TCS"])
        assert (await late.next_batch(timeout=1))[0]["price"] == 5
    asyncio.run(run())


def test_normalize_shape():
    q = normalize("NSE:TCS", {"lp": 1, "chp": 2, "update_mode": "delayed_streaming_900"})
    assert q["price"] == 1 and q["change_percent"] == 2 and q["delay_seconds"] == 900
