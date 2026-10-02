"""Python socket client against the real app, served locally (offline operations only)."""
import asyncio
import os
import socket as pysocket
import sys
import threading
import time

import pytest
import uvicorn

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "clients", "python"))

from api.config import Settings  # noqa: E402
from api.main import create_app  # noqa: E402
from tickvale import AuthError, MarketApiError, TickvaleClient  # noqa: E402


def free_port():
    with pysocket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    port = free_port()
    app = create_app(Settings(api_keys={"k": "acme"}, enable_web_ui=False, rate_limit_per_minute=1000))
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=5)


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 30))


def test_connects_and_exposes_the_hello(server):
    async def go():
        async with TickvaleClient(server, api_key="k").socket() as sock:
            assert sock.hello["service"] == "tickvale"
            return {o["name"] for o in sock.hello["operations"]}
    assert {"quotes", "candles", "markets"} <= run(go())


def test_call_returns_data_and_meta(server):
    async def go():
        async with TickvaleClient(server, api_key="k").socket() as sock:
            markets = await sock.call("markets")
            data, meta = await sock.call_full("glossary")
            return markets, data, meta, await sock.ping()
    markets, data, meta, rtt = run(go())
    assert any(m["id"] == "stocks-india" for m in markets["markets"])
    assert "pe" in data["terms"] and meta == {}
    assert 0 <= rtt < 5


def test_errors_are_typed_and_the_socket_keeps_working(server):
    async def go():
        async with TickvaleClient(server, api_key="k").socket() as sock:
            with pytest.raises(MarketApiError) as unknown:
                await sock.call("nope")
            with pytest.raises(MarketApiError) as invalid:
                await sock.call("candles", symbol="NSE:TCS", limit=0)
            return unknown.value, invalid.value, await sock.call("markets")
    unknown, invalid, still_works = run(go())
    assert unknown.code == "unknown_operation" and "Hint" in str(unknown)
    assert invalid.code == "validation_error"
    assert still_works["markets"]


def test_bad_key_raises_auth_error(server):
    async def go():
        with pytest.raises(AuthError):
            async with TickvaleClient(server, api_key="wrong").socket():
                pass
    run(go())


def test_concurrent_calls_share_one_connection(server):
    async def go():
        async with TickvaleClient(server, api_key="k").socket() as sock:
            return await asyncio.gather(*[sock.call("markets") for _ in range(5)], sock.call("glossary"))
    results = run(go())
    assert len(results) == 6 and all(results)


def test_events_end_when_the_socket_closes(server):
    async def go():
        sock = TickvaleClient(server, api_key="k").socket()
        await sock.connect()
        seen = []

        async def consume():
            async for event in sock.events():
                seen.append(event)
        task = asyncio.create_task(consume())
        await asyncio.sleep(0.1)
        await sock.close()
        await asyncio.wait_for(task, 5)
        return seen
    assert run(go()) == []


def test_closed_socket_refuses_new_requests(server):
    async def go():
        sock = TickvaleClient(server, api_key="k").socket()
        await sock.connect()
        await sock.close()
        with pytest.raises(MarketApiError):
            await sock.call("markets")
    run(go())


def test_reconnects_and_subscribes_again_after_a_drop():
    """A scripted server that hangs up on the first connection; the client must come back and resubscribe."""
    import json
    from websockets.asyncio.server import serve
    from tickvale import Socket

    seen_subscribes = []

    async def handler(ws):
        await ws.send(json.dumps({"type": "hello", "service": "tickvale", "operations": [], "channels": []}))
        connection = len(seen_subscribes) and 2 or 1
        async for raw in ws:
            frame = json.loads(raw)
            if frame.get("op") == "subscribe":
                seen_subscribes.append(frame["params"])
                await ws.send(json.dumps({"type": "subscribed", "id": frame["id"], "channel": "quotes",
                                          "symbols": ["BINANCE:BTCUSDT"], "rejected": []}))
                if connection == 1:
                    await ws.close()                    # drop it
                else:
                    await ws.send(json.dumps({"type": "quote", "data": {"symbol": "BINANCE:BTCUSDT", "price": 1}}))

    async def go():
        async with serve(handler, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            sock = Socket(f"http://127.0.0.1:{port}", None, max_backoff=1)
            await sock.connect()
            reply = await sock.subscribe("quotes", symbols=["bitcoin"])
            kinds = []
            async for event in sock.events():
                kinds.append(event["type"])
                if event["type"] == "quote":
                    break
            await sock.close()
            return reply, kinds

    reply, kinds = run(go())
    assert reply["symbols"] == ["BINANCE:BTCUSDT"]
    assert kinds == ["disconnected", "connected", "quote"]
    # second connection replayed the *resolved* symbol, not the original name
    assert seen_subscribes == [{"channel": "quotes", "symbols": ["bitcoin"]}, {"channel": "quotes", "symbols": ["BINANCE:BTCUSDT"]}]
