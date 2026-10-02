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
            mock.patch.object(QuoteHub, "_ensure_running"), \
            mock.patch.object(svc, "resolve_symbol", return_value={"query": "x", "best": None, "alternatives": []}):
        assert ws.receive_json()["type"] == "hello"
        ws.send_json({"action": "subscribe", "symbols": ["nse:tcs", "bad symbol"]})     # older frame style still works
        msg = ws.receive_json()
        assert msg["type"] == "subscribed" and msg["symbols"] == ["NSE:TCS"]
        assert msg["rejected"] == [{"symbol": "BAD SYMBOL", "reason": "not_found"}]
        ws.send_json({"action": "ping"})
        assert ws.receive_json()["type"] == "pong"


# ── live hub (no network) ──────────────────────────────────────────
def test_symbol_format_and_delay_info():
    assert valid_symbol("NSE:M&M") and valid_symbol("NSE:BAJAJ-AUTO") and not valid_symbol("nse:tcs")
    assert delay_info("delayed_streaming_900") == {"realtime": False, "delayed": True, "delay_seconds": 900, "freshness": "15 min delayed"}
    assert delay_info("streaming")["realtime"] is True and delay_info("streaming")["freshness"] == "real time"
    assert delay_info("endofday")["freshness"] == "end of day" and delay_info(None)["freshness"] == "unknown"


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


# ── help for people new to markets ────────────────────────────────
def test_help_endpoints_are_public_and_plain():
    with make() as c:
        markets = c.get("/v1/markets").json()["data"]
        terms = c.get("/v1/glossary").json()["data"]
    assert {m["id"] for m in markets["markets"]} >= {"stocks-india", "crypto"}
    assert any(t["id"] == "1d" for t in markets["timeframes"])
    assert "pe" in terms["terms"] and "profit" in terms["terms"]["pe"]["plain"].lower()
    assert terms["field_terms"]["market_cap_basic"] == "market_cap"
    # every field mapping points at a real glossary entry
    assert set(terms["field_terms"].values()) <= set(terms["terms"])


def test_errors_carry_a_hint():
    with make() as c:
        missing = c.get("/v1/quotes?symbols=NSE:TCS").json()["error"]
        bad = c.get("/v1/quotes?symbols=reliance", headers=H).json()["error"]
    assert "X-API-Key" in missing["hint"]
    assert "/v1/symbols/resolve?q=reliance" in bad["hint"]


def test_resolve_endpoint_and_country_preference():
    hits = [
        {"exchange": "MYX", "symbol": "TCS", "description": "TCS Group Holdings Bhd", "type": "stock", "country": "MY", "primary": True},
        {"exchange": "NSE", "symbol": "TCS", "description": "Tata Consultancy Services Limited", "type": "stock", "country": "IN", "primary": True},
    ]
    with make() as c, mock.patch.object(svc, "search_symbols_raw", return_value=hits):
        india = c.get("/v1/symbols/resolve?q=tcs", headers=H).json()["data"]
        us_pref = c.get("/v1/symbols/resolve?q=tcs&country=MY", headers=H).json()["data"]
    assert india["best"]["full_symbol"] == "NSE:TCS" and india["alternatives"][0]["full_symbol"] == "MYX:TCS"
    assert us_pref["best"]["full_symbol"] == "MYX:TCS"


def test_resolve_known_coin_and_qualified_symbol_skip_search():
    with mock.patch.object(svc, "search_symbols_raw", side_effect=AssertionError("should not search")):
        assert svc.resolve_symbol("bitcoin")["best"]["full_symbol"] == "BINANCE:BTCUSDT"
        assert svc.resolve_symbol("nse:tcs")["best"]["full_symbol"] == "NSE:TCS"


def test_resolve_nothing_found_is_404_with_hint():
    with make() as c, mock.patch.object(svc, "search_symbols_raw", return_value=[]):
        r = c.get("/v1/symbols/resolve?q=zzzzqq", headers=H)
    assert r.status_code == 404 and r.json()["error"]["hint"]


def test_path_parts_are_validated():
    with make() as c:
        r = c.get("/v1/symbols/NSE/..%2F..%2Fetc/fundamentals", headers=H)
        assert r.status_code in (400, 404)
        assert c.get("/v1/symbols/%3Cscript%3E/X", headers=H).status_code == 400
    with pytest.raises(Exception):
        svc.check_symbol_parts("NSE", "a" * 80)


def test_screener_columns_must_look_like_field_names():
    with make() as c:
        r = c.post("/v1/screener", json={"columns": ["close", "x; drop"]}, headers=H)
    assert r.status_code == 422


# ── per-product clients: own key, permissions, limits ─────────────
def clients_settings(tmp_path, entries, **kw):
    import json as _json
    from api.config import load_clients_file
    f = tmp_path / "clients.json"
    f.write_text(_json.dumps(entries))
    plain, hashed, policies = load_clients_file(str(f))
    return Settings(api_keys=plain, hashed_keys=hashed, clients=policies, enable_web_ui=False, **kw)


def test_hashed_key_authenticates_and_limits_apply(tmp_path):
    from api.config import sha256_hex
    entries = [{"name": "nesto", "key_sha256": sha256_hex("nesto-secret-key-1234"), "operations": ["quotes"],
                "rate_limit_per_minute": 2, "max_symbols": 2}]
    with TestClient(create_app(clients_settings(tmp_path, entries))) as c, \
            mock.patch.object(svc, "snapshot_quotes", return_value=([], [])):
        ok = c.get("/v1/quotes?symbols=NSE:A", headers={"X-API-Key": "nesto-secret-key-1234"})
        assert ok.status_code == 200 and ok.headers["x-ratelimit-limit"] == "2"
        too_many = c.get("/v1/quotes?symbols=NSE:A,NSE:B,NSE:C", headers={"X-API-Key": "nesto-secret-key-1234"})
        assert too_many.status_code == 400 and "At most 2" in too_many.json()["error"]["message"]
        assert c.get("/v1/quotes?symbols=NSE:A", headers={"X-API-Key": "nesto-secret-key-1234"}).status_code == 429
        assert c.get("/v1/quotes?symbols=NSE:A", headers={"X-API-Key": sha256_hex("nesto-secret-key-1234")}).status_code == 401


def test_a_client_can_only_use_its_operations(tmp_path):
    entries = [{"name": "reports", "key": "reports-secret-key-1234", "operations": ["fundamentals"]}]
    with TestClient(create_app(clients_settings(tmp_path, entries))) as c:
        denied = c.get("/v1/markets/movers", headers={"X-API-Key": "reports-secret-key-1234"})
        public = c.get("/v1/markets")                      # help is always open
    err = denied.json()["error"]
    assert denied.status_code == 403 and err["code"] == "forbidden" and "fundamentals" in err["hint"]
    assert public.status_code == 200


def test_socket_hello_and_requests_follow_the_clients_permissions(tmp_path):
    entries = [{"name": "nesto", "key": "nesto-secret-key-1234", "operations": ["quotes", "candles"],
                "channels": ["quotes"], "max_symbols": 1, "rate_limit_per_minute": 50}]
    hdr = {"X-API-Key": "nesto-secret-key-1234"}
    with TestClient(create_app(clients_settings(tmp_path, entries))) as c, \
            c.websocket_connect("/v1/ws", headers=hdr) as ws, mock.patch.object(QuoteHub, "_ensure_running"):
        hello = ws.receive_json()
        names = {o["name"] for o in hello["operations"]}
        assert {"quotes", "candles", "markets", "ping"} <= names and "screener" not in names     # help stays visible
        assert [ch["name"] for ch in hello["channels"]] == ["quotes"]
        assert hello["limits"]["max_symbols"] == 1 and hello["limits"]["requests_per_minute"] == 50
        ws.send_json({"id": "1", "op": "screener", "params": {}})
        assert ws.receive_json()["code"] == "forbidden"
        ws.send_json({"id": "2", "op": "subscribe", "params": {"channel": "movers"}})
        assert ws.receive_json()["code"] == "forbidden"
        ws.send_json({"id": "3", "op": "subscribe", "params": {"channel": "quotes", "symbols": ["NSE:A", "NSE:B"]}})
        sub = ws.receive_json()
        assert sub["symbols"] == ["NSE:A"] and sub["rejected"] == [{"symbol": "NSE:B", "reason": "too_many_symbols"}]


def test_clients_file_rejects_mistakes_with_clear_messages(tmp_path):
    import json as _json
    from api.config import load_clients_file

    def check(entries, text):
        f = tmp_path / "bad.json"
        f.write_text(_json.dumps(entries))
        with pytest.raises(ValueError) as e:
            load_clients_file(str(f))
        assert text in str(e.value)

    check([{"name": "a b", "key": "x" * 20}], "'name' must be")
    check([{"name": "a"}], "exactly one of")
    check([{"name": "a", "key": "short"}], "at least 16")
    check([{"name": "a", "key_sha256": "zz"}], "64 hex")
    check([{"name": "a", "key": "x" * 20}, {"name": "a", "key": "y" * 20}], "used twice")
    check([{"name": "a", "key": "x" * 20}, {"name": "b", "key": "x" * 20}], "already used")
    check([{"name": "a", "key": "x" * 20, "operations": "quotes"}], "list of names")
    check([{"name": "a", "key": "x" * 20, "max_symbols": 0}], "positive")


def test_key_tool_creates_a_hashed_entry(tmp_path, capsys):
    import json as _json
    from api import keys
    from api.config import load_clients_file, sha256_hex
    target = tmp_path / "clients.json"
    assert keys.main(["nesto", "--operations", "quotes,candles", "--channels", "quotes", "--rate", "300", "--append", str(target)]) == 0
    printed = capsys.readouterr().out
    key = [line.strip() for line in printed.splitlines() if len(line.strip()) >= 30 and " " not in line.strip()][0]
    entry = _json.loads(target.read_text())[0]
    assert entry["key_sha256"] == sha256_hex(key) and "key" not in entry and key not in target.read_text()
    assert entry["operations"] == ["quotes", "candles"] and entry["rate_limit_per_minute"] == 300
    assert load_clients_file(str(target))[2]["nesto"].allows_operation("quotes")
    assert keys.main(["nesto", "--append", str(target)]) == 2            # no duplicates
    assert keys.main(["bad name"]) == 2
