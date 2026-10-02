"""WebSocket protocol tests (offline): hello, requests, subscriptions, limits, errors."""
from unittest import mock

import pytest
from starlette.websockets import WebSocketDisconnect

from api import services as svc
from api.live import QuoteHub
from tests.test_v1 import H, make


@pytest.fixture(autouse=True)
def clear_cache():
    svc._CACHE.clear()
    yield
    svc._CACHE.clear()


def connect(c, headers=H):
    ws = c.websocket_connect("/v1/ws", headers=headers)
    return ws


def test_hello_describes_the_service():
    with make() as c, connect(c) as ws:
        hello = ws.receive_json()
    assert hello["type"] == "hello" and hello["service"] == "tickvale" and hello["auth"] == "api_key"
    names = {o["name"] for o in hello["operations"]}
    assert {"quotes", "candles", "screener", "movers", "resolve", "markets", "glossary"} <= names
    assert {c["name"] for c in hello["channels"]} == {"quotes", "movers"}
    assert hello["limits"]["max_symbols"] == 100 and "delayed" in hello["note"]
    assert hello["how_to"]["ask"]["op"] == "quotes"


def test_request_gets_a_result_with_the_same_id():
    quote = {"symbol": "NSE:TCS", "status": "ok", "price": 1.0}
    with make() as c, connect(c) as ws, mock.patch.object(svc, "snapshot_quotes", return_value=([quote], [])):
        ws.receive_json()
        ws.send_json({"id": "7", "op": "quotes", "params": {"symbols": ["NSE:TCS"]}})
        msg = ws.receive_json()
    assert msg["type"] == "result" and msg["id"] == "7" and msg["op"] == "quotes"
    assert msg["data"] == [quote] and msg["meta"]["count"] == 1


def test_plain_names_are_resolved_on_the_socket():
    resolved = {"query": "reliance", "best": {"full_symbol": "NSE:RELIANCE"}, "alternatives": []}
    with make() as c, connect(c) as ws, \
            mock.patch.object(svc, "resolve_symbol", return_value=resolved), \
            mock.patch.object(svc, "snapshot_quotes", return_value=([], [])) as snap:
        ws.receive_json()
        ws.send_json({"id": "1", "op": "quotes", "params": {"symbols": "reliance"}})
        assert ws.receive_json()["type"] == "result"
    assert snap.call_args.args[0] == ["NSE:RELIANCE"]


def test_errors_keep_the_connection_open_and_carry_hints():
    with make() as c, connect(c) as ws:
        ws.receive_json()
        ws.send_json({"id": "a", "op": "nope"})
        unknown = ws.receive_json()
        ws.send_json({"id": "b", "op": "candles", "params": {"symbol": "NSE:TCS", "limit": 0}})
        invalid = ws.receive_json()
        ws.send_json({"id": "c", "op": "subscribe", "params": {"channel": "weather"}})
        channel = ws.receive_json()
        ws.send_json({"op": "ping"})
        pong = ws.receive_json()
    assert unknown["type"] == "error" and unknown["id"] == "a" and unknown["code"] == "unknown_operation" and "quotes" in unknown["hint"]
    assert invalid["code"] == "validation_error" and invalid["id"] == "b"
    assert channel["code"] == "unknown_channel" and "quotes" in channel["hint"]
    assert pong["type"] == "pong"


def test_unknown_parameters_are_rejected_not_ignored():
    with make() as c, connect(c) as ws:
        ws.receive_json()
        ws.send_json({"id": "x", "op": "markets", "params": {"typo": 1}})
        msg = ws.receive_json()
    assert msg["type"] == "error" and msg["code"] == "validation_error"


def test_malformed_frames_get_help_and_eventually_a_hangup():
    with make() as c, connect(c) as ws:
        ws.receive_json()
        ws.send_text("not json")
        first = ws.receive_json()
        assert first["code"] == "bad_message" and "how_to" in first["hint"]
        with pytest.raises(WebSocketDisconnect) as e:
            for _ in range(15):
                ws.send_text("[1,2,3]")
                ws.receive_json()
        assert e.value.code == 4400


def test_each_request_counts_against_the_rate_limit_but_the_socket_survives():
    with make(rate_limit_per_minute=3) as c, connect(c) as ws:      # 1 hit is spent on connecting
        ws.receive_json()
        replies = []
        for i in range(3):
            ws.send_json({"id": str(i), "op": "markets"})
            replies.append(ws.receive_json())
        ws.send_json({"op": "ping"})
        assert ws.receive_json()["type"] == "pong"                  # pings are free
    kinds = [r["type"] if r["type"] == "result" else r["code"] for r in replies]
    assert kinds.count("result") == 2 and "rate_limited" in kinds


def test_quote_subscription_flow_reports_resolved_and_rejected():
    def fake_resolve(text, country=""):
        if text.lower() == "bitcoin":
            return {"query": text, "best": {"full_symbol": "BINANCE:BTCUSDT"}, "alternatives": []}
        return {"query": text, "best": None, "alternatives": []}

    with make() as c, connect(c) as ws, mock.patch.object(QuoteHub, "_ensure_running"), \
            mock.patch.object(svc, "resolve_symbol", side_effect=fake_resolve):
        ws.receive_json()
        ws.send_json({"id": "s", "op": "subscribe", "params": {"channel": "quotes", "symbols": ["NSE:TCS", "bitcoin", "zzzz"]}})
        msg = ws.receive_json()
        ws.send_json({"id": "u", "op": "unsubscribe", "params": {"channel": "quotes", "symbols": ["NSE:TCS"]}})
        gone = ws.receive_json()
    assert msg["type"] == "subscribed" and msg["id"] == "s" and msg["channel"] == "quotes"
    assert msg["symbols"] == ["NSE:TCS", "BINANCE:BTCUSDT"]
    assert msg["resolved"] == {"bitcoin": "BINANCE:BTCUSDT"}
    assert msg["rejected"] == [{"symbol": "ZZZZ", "reason": "not_found"}]
    assert gone["type"] == "unsubscribed" and gone["symbols"] == ["NSE:TCS"]


def test_symbol_cap_per_connection():
    with make(ws_max_symbols_per_client=2) as c, connect(c) as ws, mock.patch.object(QuoteHub, "_ensure_running"):
        ws.receive_json()
        ws.send_json({"op": "subscribe", "params": {"channel": "quotes", "symbols": ["NSE:A", "NSE:B", "NSE:C"]}})
        msg = ws.receive_json()
    assert msg["symbols"] == ["NSE:A", "NSE:B"] and msg["rejected"] == [{"symbol": "NSE:C", "reason": "too_many_symbols"}]


def test_movers_channel_pushes_updates():
    rows = [{"symbol": "NSE:ABC", "close": 1.0, "change": 2.0}]
    with make() as c, connect(c) as ws, \
            mock.patch.object(svc.movers_scraper, "scrape", return_value={"status": "success", "data": rows}):
        ws.receive_json()
        ws.send_json({"id": "m", "op": "subscribe", "params": {"channel": "movers", "market": "stocks-india", "category": "gainers"}})
        sub = ws.receive_json()
        update = ws.receive_json()
    assert sub["type"] == "subscribed" and sub["key"] == "stocks-india:gainers" and sub["interval"] == 30
    assert update["type"] == "update" and update["channel"] == "movers" and update["data"] == rows


def test_movers_channel_rejects_bad_input_up_front():
    with make() as c, connect(c) as ws:
        ws.receive_json()
        ws.send_json({"id": "m", "op": "subscribe", "params": {"channel": "movers", "market": "crypto", "category": "penny-stocks"}})
        msg = ws.receive_json()
    assert msg["type"] == "error" and msg["id"] == "m"


def test_rest_and_socket_share_one_implementation():
    from api import operations as ops
    rest_ops = {"quotes", "symbol", "fundamentals", "technicals", "candles", "news", "movers", "screener", "earnings", "dividends", "resolve", "search"}
    assert rest_ops <= set(ops.REGISTRY)


def test_open_mode_needs_no_key():
    with make(api_keys={}) as c, c.websocket_connect("/v1/ws") as ws:
        hello = ws.receive_json()
    assert hello["auth"] == "open" and hello["client"].startswith("ip:")


def test_operations_endpoint_lists_everything_publicly():
    with make() as c:
        data = c.get("/v1/operations").json()["data"]
    assert any(o["name"] == "quotes" for o in data)


# ── extending: plugins add operations and channels without touching core code ──
def test_plugin_module_adds_an_operation_and_a_channel(tmp_path, monkeypatch):
    import sys
    from api import operations as ops
    from api import socket as sock

    (tmp_path / "demo_plugin.py").write_text(
        "import asyncio\n"
        "from pydantic import Field\n"
        "from api.operations import Params, Result, operation\n"
        "from api.socket import Channel, channel\n\n"
        "class EchoParams(Params):\n"
        "    text: str = Field(..., max_length=20)\n\n"
        "@operation('demo.echo', 'Repeat a word back.', EchoParams)\n"
        "def echo(p, ctx):\n"
        "    return Result({'echo': p.text})\n\n"
        "@channel\n"
        "class Clock(Channel):\n"
        "    name = 'demo.clock'\n"
        "    summary = 'Sends one tick.'\n"
        "    async def subscribe(self, session, params):\n"
        "        await session.send({'type': 'update', 'channel': 'demo.clock', 'data': 'tick'})\n"
        "        return {'channel': 'demo.clock'}\n"
        "    async def unsubscribe(self, session, params):\n"
        "        return {'channel': 'demo.clock'}\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        with make(plugins=["demo_plugin"]) as c, connect(c) as ws:
            hello = ws.receive_json()
            assert "demo.echo" in {o["name"] for o in hello["operations"]}
            assert "demo.clock" in {ch["name"] for ch in hello["channels"]}
            ws.send_json({"id": "e", "op": "demo.echo", "params": {"text": "hi"}})
            assert ws.receive_json()["data"] == {"echo": "hi"}
            ws.send_json({"id": "bad", "op": "demo.echo", "params": {"text": "x" * 50}})
            assert ws.receive_json()["code"] == "validation_error"
            ws.send_json({"id": "s", "op": "subscribe", "params": {"channel": "demo.clock"}})
            kinds = sorted(ws.receive_json()["type"] for _ in range(2))
            assert kinds == ["subscribed", "update"]
            assert c.get("/v1/operations").json()["data"]       # also listed publicly
    finally:
        ops.REGISTRY.pop("demo.echo", None)
        sock.CHANNELS.pop("demo.clock", None)
        sys.modules.pop("demo_plugin", None)


def test_duplicate_operation_names_are_refused():
    from api import operations as ops
    with pytest.raises(ValueError):
        ops.operation("quotes", "again", ops.NoParams)(lambda p, ctx: None)
