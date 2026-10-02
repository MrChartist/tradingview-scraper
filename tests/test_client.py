"""Offline tests for the Python SDK (clients/python): retries, error mapping, request shape."""
import os
import sys
from unittest import mock

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "clients", "python"))

from tickvale import (AuthError, ConnectionFailed, MarketClient, NotFoundError,  # noqa: E402
                                RateLimitError)


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body or {}, headers or {}
        self.ok = status < 400
        self.text = str(body)

    def json(self):
        return self._body


def ok(data, **meta):
    return FakeResponse(200, {"data": data, "meta": {"request_id": "r1", **meta}})


def err(status, code, message, headers=None):
    return FakeResponse(status, {"error": {"code": code, "message": message, "request_id": "r9"}}, headers)


@pytest.fixture
def client():
    with mock.patch("time.sleep") as sleep:
        c = MarketClient("https://api.test/", api_key="k", max_retries=2)
        c._sleep = sleep
        yield c


def test_sends_key_and_unwraps_data(client):
    with mock.patch.object(client.session, "request", return_value=ok([{"symbol": "NSE:TCS"}])) as r:
        assert client.quotes(["NSE:TCS", "NASDAQ:AAPL"]) == [{"symbol": "NSE:TCS"}]
    assert r.call_args.args[:2] == ("GET", "https://api.test/v1/quotes")
    assert r.call_args.kwargs["params"] == {"symbols": "NSE:TCS,NASDAQ:AAPL"}
    assert client.session.headers["X-API-Key"] == "k"


def test_quotes_with_meta(client):
    with mock.patch.object(client.session, "request", return_value=ok([], not_found=["NSE:X"], count=0)):
        data, meta = client.quotes("NSE:X", with_meta=True)
    assert data == [] and meta["not_found"] == ["NSE:X"]


def test_quote_not_found_raises(client):
    with mock.patch.object(client.session, "request", return_value=ok([], not_found=["NSE:X"])):
        with pytest.raises(NotFoundError):
            client.quote("NSE:X")


@pytest.mark.parametrize("status,cls", [(401, AuthError), (403, AuthError), (404, NotFoundError)])
def test_errors_are_typed_and_not_retried(client, status, cls):
    with mock.patch.object(client.session, "request", return_value=err(status, "x", "nope")) as r:
        with pytest.raises(cls) as e:
            client.symbol("NSE:TCS")
    assert r.call_count == 1 and e.value.request_id == "r9" and e.value.status == status


def test_429_retries_honouring_retry_after_then_succeeds(client):
    responses = [err(429, "rate_limited", "slow", {"Retry-After": "3"}), ok([1])]
    with mock.patch.object(client.session, "request", side_effect=responses) as r, mock.patch("time.sleep") as sleep:
        assert client.quotes("NSE:TCS") == [1]
    assert r.call_count == 2 and sleep.call_args.args[0] == 3.0


def test_gives_up_after_max_retries(client):
    with mock.patch.object(client.session, "request", return_value=err(429, "rate_limited", "slow", {"Retry-After": "1"})) as r, \
            mock.patch("time.sleep"):
        with pytest.raises(RateLimitError) as e:
            client.quotes("NSE:TCS")
    assert r.call_count == 3 and e.value.retry_after == 1.0


def test_upstream_error_retried(client):
    with mock.patch.object(client.session, "request", side_effect=[err(502, "upstream_error", "bad"), ok([])]) as r, \
            mock.patch("time.sleep"):
        client.movers()
    assert r.call_count == 2


def test_network_errors_become_connection_failed(client):
    with mock.patch.object(client.session, "request", side_effect=requests.ConnectionError("down")), mock.patch("time.sleep"):
        with pytest.raises(ConnectionFailed):
            client.health()


def test_screener_body_and_symbol_validation(client):
    with mock.patch.object(client.session, "request", return_value=ok([])) as r:
        client.screener("india", [{"field": "close", "op": "gt", "value": 1}], limit=5)
    body = r.call_args.kwargs["json"]
    assert body["market"] == "india" and body["limit"] == 5 and body["conditions"][0]["op"] == "gt"
    strict = MarketClient("https://api.test", auto_resolve=False)
    with pytest.raises(ValueError):
        strict.symbol("RELIANCE")


def test_candles_dataframe(client):
    pd = pytest.importorskip("pandas")
    rows = [{"time": 1700000000, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 9}]
    with mock.patch.object(client.session, "request", return_value=ok(rows)):
        df = client.candles("NSE:TCS", as_dataframe=True)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"] and isinstance(df.index, pd.DatetimeIndex)


def test_plain_names_are_resolved_once_and_cached(client):
    resolve = ok({"query": "reliance", "best": {"full_symbol": "NSE:RELIANCE"}, "alternatives": []})
    quote = ok([{"symbol": "NSE:RELIANCE", "price": 1}])
    with mock.patch.object(client.session, "request", side_effect=[resolve, quote, quote]) as r:
        client.quote("Reliance")
        client.quote("reliance")           # second call reuses the cached resolution
    urls = [call.args[1] for call in r.call_args_list]
    assert urls == ["https://api.test/v1/symbols/resolve", "https://api.test/v1/quotes", "https://api.test/v1/quotes"]
    assert r.call_args_list[1].kwargs["params"] == {"symbols": "NSE:RELIANCE"}


def test_qualified_symbols_skip_resolution_and_resolution_can_be_disabled():
    with mock.patch("time.sleep"):
        c = MarketClient("https://api.test", auto_resolve=False)
        with pytest.raises(ValueError):
            c.symbol("reliance")
        with mock.patch.object(c.session, "request", return_value=ok([])) as r:
            c.quotes("nse:tcs")
    assert r.call_args.kwargs["params"] == {"symbols": "NSE:TCS"}
