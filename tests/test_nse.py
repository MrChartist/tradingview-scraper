"""NSE official MCP source: client protocol, answer mapping (using real response shapes), fallback."""
import json
from unittest import mock

import pytest
import requests
from fastapi.testclient import TestClient

from api import markets_india as india
from api import services as svc
from api.config import Settings
from api.main import create_app
from api.providers import NotSupported
from api.providers.mcp import McpClient, McpError
from api.providers.nse import NseProvider, nse_ticker, tv_ticker
from tests.test_v1 import H

LIVE_QUOTE = {"updatedAt": "2026-10-02T15:23:27Z", "stock": {
    "type": "CM", "symbol": "RELIANCE", "series": "EQ", "openPrice": 1180.1, "highPrice": 1183.9, "lowPrice": 1160.8,
    "preClosePrice": 1187.0, "lastTradedPrice": 1167.7, "change": -19.3, "perChange": -1.63, "volume": 16771221,
    "fiftyTwoWeekHigh": 1611.8, "fiftyTwoWeekLow": 1160.8, "latestTimestamp": "2026-10-01 16:00:28"}}


class FakeMcp:
    """Stands in for McpClient: answers by tool name; a value may be a callable or an Exception."""

    def __init__(self, answers, url="fake://mcp"):
        self.answers, self.url, self.calls = answers, url, []

    def call(self, tool, arguments=None, timeout=None):
        self.calls.append((tool, arguments))
        answer = self.answers[tool]
        if callable(answer):
            answer = answer(arguments)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture(autouse=True)
def fresh_cache():
    svc._CACHE.clear()
    yield
    svc._CACHE.clear()


def provider(live=None, eod=None):
    return NseProvider(Settings(), live=FakeMcp(live or {}), eod=FakeMcp(eod or {}))


def closed():
    return mock.patch.object(india, "is_trading_hours", return_value=False)


# ── helpers ────────────────────────────────────────────────────────
def test_ticker_spelling_between_nse_and_tradingview():
    assert nse_ticker("BAJAJ_AUTO") == "BAJAJ-AUTO" and tv_ticker("BAJAJ-AUTO") == "BAJAJ_AUTO"
    assert nse_ticker("M&M") == "M&M"


def test_market_hours_helpers():
    ist = india.IST
    import datetime as dt
    assert india.is_trading_hours(dt.datetime(2026, 10, 1, 10, 0, tzinfo=ist))          # Thursday morning
    assert not india.is_trading_hours(dt.datetime(2026, 10, 1, 16, 0, tzinfo=ist))      # after close
    assert not india.is_trading_hours(dt.datetime(2026, 10, 3, 11, 0, tzinfo=ist))      # Saturday
    assert india.last_trading_day(dt.datetime(2026, 10, 5, 8, 0, tzinfo=ist)) == dt.date(2026, 10, 2)   # Monday before open -> Friday
    assert india.parse_ist("2026-10-01 16:00:28") == int(dt.datetime(2026, 10, 1, 16, 0, 28, tzinfo=ist).timestamp())
    assert india.describe_time(india.parse_ist("2026-10-01 16:00:28")) == "1 Oct, 4:00 pm IST"


# ── quotes ─────────────────────────────────────────────────────────
def test_live_quote_is_mapped_and_says_when_the_market_is_closed():
    p = provider(live={"cm_get_stock_quote": LIVE_QUOTE})
    with closed():
        found, missing = p.quotes(["NSE:RELIANCE"])
    q = found[0]
    assert missing == [] and q["symbol"] == "NSE:RELIANCE" and q["price"] == 1167.7 and q["change_percent"] == -1.63
    assert q["currency"] == "INR" and q["prev_close"] == 1187.0 and q["high_52w"] == 1611.8
    assert q["freshness"] == "market closed. Last price 1 Oct, 4:00 pm IST" and q["market_open"] is False
    assert q["delayed"] is True and q["realtime"] is False


def test_live_quote_during_trading_hours_is_about_five_minutes_behind():
    fresh = json.loads(json.dumps(LIVE_QUOTE))
    fresh["stock"]["latestTimestamp"] = india.now_ist().strftime("%Y-%m-%d %H:%M:%S")
    p = provider(live={"cm_get_stock_quote": fresh})
    with mock.patch.object(india, "is_trading_hours", return_value=True):
        q = p.quotes(["NSE:RELIANCE"])[0][0]
    assert q["freshness"] == "about 5 min delayed" and q["market_open"] is True and q["delay_seconds"] == 300


def test_stale_prices_during_hours_hint_at_a_holiday():
    p = provider(live={"cm_get_stock_quote": LIVE_QUOTE})          # last trade is days old
    with mock.patch.object(india, "is_trading_hours", return_value=True):
        q = p.quotes(["NSE:RELIANCE"])[0][0]
    assert "market may be closed" in q["freshness"] and q["market_open"] is False


def test_names_are_sent_in_the_exchanges_spelling_and_unknown_ones_are_reported():
    live = FakeMcp({"cm_get_stock_quote": lambda a: {"error": "Symbol 'X' not found"} if a["symbol"] == "NOPE" else LIVE_QUOTE})
    p = NseProvider(Settings(), live=live, eod=FakeMcp({}))
    found, missing = p.quotes(["NSE:BAJAJ_AUTO", "NSE:NOPE"])
    assert sorted(a["symbol"] for _, a in live.calls) == ["BAJAJ-AUTO", "NOPE"]
    assert len(found) == 1 and missing == ["NSE:NOPE"]


def test_if_every_call_fails_the_error_is_raised_so_another_source_can_answer():
    p = provider(live={"cm_get_stock_quote": McpError("down")})
    with pytest.raises(McpError):
        p.quotes(["NSE:RELIANCE", "NSE:TCS"])


def test_many_symbols_use_one_end_of_day_call():
    syms = [f"NSE:S{i}" for i in range(40)]
    bulk = {"quotes": [{"symbol": "S1", "close": 110.0, "open": 100, "high": 111, "low": 99, "prev_close": 100.0, "pct_change": 10.0,
                        "volume": 5, "date": "2026-10-01"}]}
    p = provider(eod={"get_bulk_quote": bulk})
    found, missing = p.quotes(syms)
    assert len(p.eod.calls) == 1 and p.live.calls == []
    assert found[0]["symbol"] == "NSE:S1" and found[0]["change"] == 10.0 and found[0]["freshness"] == "end of day (2026-10-01)"
    assert len(missing) == 39


# ── candles ────────────────────────────────────────────────────────
def rows(*dates):
    return [{"date": d, "open": 1, "high": 3, "low": 0.5, "close": 2 + i, "volume": 10} for i, d in enumerate(dates)]


def test_daily_candles_use_the_same_timestamp_as_other_sources_and_walk_back_through_chunks():
    pages = iter([{"data": rows("2026-09-30", "2026-10-01"), "next_end_date": "2026-09-29"},
                  {"data": rows("2026-09-28", "2026-09-29"), "next_end_date": None}])
    p = provider(eod={"get_stock_history": lambda a: next(pages)})
    out = p.candles("NSE", "TCS", "1d", 4)
    assert [c["close"] for c in out] == [2, 3, 2, 3] and len(out) == 4
    assert out[-1]["timestamp"] == india.day_start_epoch("2026-10-01")
    assert [c["timestamp"] for c in out] == sorted(c["timestamp"] for c in out)


def test_only_daily_candles_and_unknown_symbols_hand_over():
    p = provider(eod={"get_stock_history": {"data": []}})
    with pytest.raises(NotSupported):
        p.candles("NSE", "TCS", "5m", 5)
    with pytest.raises(NotSupported):
        p.candles("NSE", "NIFTY", "1d", 5)


# ── movers ─────────────────────────────────────────────────────────
def item(sym, ltp, prev, pct, qty, series="EQ"):
    return {"symbol": sym, "series": series, "ltp": ltp, "prev_price": prev, "net_price": pct, "trade_quantity": qty}


GAINERS = {"data": {"legends": [["NIFTY", "NIFTY 50"]],
                    "NIFTY": {"data": [item("INFY", 1035, 994.1, 4.11, 15_000_000), item("BAJAJ-AUTO", 10045, 9000, 11.6, 300_000)]},
                    "SecGtr20": {"data": [item("SMLT", 74.18, 61.82, 19.99, 112_524), item("INFY", 1035, 994.1, 4.11, 15_000_000),
                                          item("THIN", 50, 40, 25.0, 500), item("ODD", 10, 9, 11.1, 900_000, series="BE")]}}}


def test_movers_are_flattened_deduplicated_liquid_and_sorted():
    p = provider(live={"cm_get_live_gainers": GAINERS})
    out = p.movers("stocks-india", "gainers", 10)
    symbols = [r["symbol"] for r in out["data"]]
    assert symbols == ["NSE:SMLT", "NSE:BAJAJ_AUTO", "NSE:INFY"]          # THIN (illiquid) and ODD (not EQ) dropped; INFY once
    top = out["data"][0]
    assert top["change"] == 19.99 and top["change_abs"] == 12.36 and top["volume"] == 112_524 and top["currency"] == "INR"
    assert provider(live={"cm_get_live_gainers": GAINERS}).movers("stocks-india", "gainers", 1)["data"][0]["symbol"] == "NSE:SMLT"


def test_movers_losers_sort_ascending_and_other_requests_hand_over():
    losers = {"data": {"NIFTY": {"data": [item("A", 90, 100, -10.0, 200_000), item("B", 95, 100, -5.0, 200_000)]}}}
    p = provider(live={"cm_get_live_losers": losers})
    assert [r["symbol"] for r in p.movers("stocks-india", "losers", 5)["data"]] == ["NSE:A", "NSE:B"]
    for market, category in (("stocks-usa", "gainers"), ("stocks-india", "most-active")):
        with pytest.raises(NotSupported):
            p.movers(market, category, 5)


# ── corporate actions and breadth ──────────────────────────────────
def test_corporate_actions_are_renamed_to_plain_keys():
    payload = {"count": 1, "actions": [{"exDate": "2026-06-05", "actionType": "DIVIDEND", "purpose": "Dividend - Rs 6 Per Share", "adjustmentFactor": 1.0}]}
    p = provider(eod={"get_corporate_actions": payload})
    assert p.corporate_actions("NSE", "RELIANCE", "2026-01-01", "2026-10-01") == [
        {"ex_date": "2026-06-05", "type": "DIVIDEND", "purpose": "Dividend - Rs 6 Per Share", "adjustment_factor": 1.0}]
    assert p.eod.calls[0][1] == {"symbol": "RELIANCE", "fromDate": "2026-01-01", "toDate": "2026-10-01"}


def test_breadth_without_a_date_walks_back_to_a_day_with_data():
    answers = iter([{"date": "x", "total_stocks": 0}, {"date": "2026-09-30", "total_stocks": 3700, "advances": 900, "declines": 2700,
                                                      "unchanged": 100, "ad_ratio": 0.33, "total_volume": 5}])
    p = provider(eod={"get_market_breadth": lambda a: next(answers)})
    out = p.market_breadth("NSE", None)
    assert out["advances"] == 900 and out["advance_decline_ratio"] == 0.33 and len(p.eod.calls) == 2
    with pytest.raises(NotSupported):
        provider(eod={"get_market_breadth": {"total_stocks": 0}}).market_breadth("NSE", "2026-10-02")


# ── the MCP client itself ──────────────────────────────────────────
class Reply:
    def __init__(self, status=200, body=None, headers=None, sse=False):
        self.status_code, self._body, self.headers = status, body, dict(headers or {})
        self.text = ("event: message\ndata: " + json.dumps(body) + "\n\n") if sse else (json.dumps(body) if body is not None else "")
        if sse:
            self.headers["content-type"] = "text/event-stream"
        else:
            self.headers["content-type"] = "application/json"

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


def tool_result(payload, error=False):
    return {"jsonrpc": "2.0", "id": 1, "result": {"isError": error, "content": [{"type": "text", "text": payload if isinstance(payload, str) else json.dumps(payload)}]}}


def test_client_starts_a_session_once_and_sends_its_id_with_each_call():
    client = McpClient("https://mcp.test/x")
    sent = []

    def post(url, headers, json, timeout):
        sent.append((json.get("method"), headers.get("Mcp-Session-Id")))
        if json.get("method") == "initialize":
            return Reply(body={"jsonrpc": "2.0", "id": 1, "result": {}}, headers={"mcp-session-id": "S1"})
        if json.get("method") == "notifications/initialized":
            return Reply(202)
        return Reply(body=tool_result({"ok": 1}))

    with mock.patch.object(client._http, "post", side_effect=post):
        assert client.call("t", {"a": 1}) == {"ok": 1}
        assert client.call("t") == {"ok": 1}
    assert [m for m, _ in sent].count("initialize") == 1
    assert [s for m, s in sent if m == "tools/call"] == ["S1", "S1"]


def test_client_reads_event_stream_answers_and_plain_text():
    client = McpClient("https://mcp.test/x")
    answers = iter([Reply(body={"jsonrpc": "2.0", "id": 1, "result": {}}, headers={"mcp-session-id": "S"}), Reply(202),
                    Reply(body=tool_result({"n": 2}), sse=True), Reply(body=tool_result("plain words"))])
    with mock.patch.object(client._http, "post", side_effect=lambda *a, **k: next(answers)):
        assert client.call("t") == {"n": 2}
        assert client.call("t") == "plain words"


def test_client_recovers_once_when_the_server_forgets_the_session():
    client = McpClient("https://mcp.test/x")
    log = []

    def post(url, headers, json, timeout):
        method = json.get("method")
        log.append(method)
        if method == "initialize":
            return Reply(body={"jsonrpc": "2.0", "id": 1, "result": {}}, headers={"mcp-session-id": f"S{log.count('initialize')}"})
        if method == "notifications/initialized":
            return Reply(202)
        if headers.get("Mcp-Session-Id") == "S1":
            return Reply(404)                                  # first session is gone
        return Reply(body=tool_result({"back": True}))

    with mock.patch.object(client._http, "post", side_effect=post):
        assert client.call("t") == {"back": True}
    assert log.count("initialize") == 2


def test_client_raises_on_tool_errors_and_protocol_errors():
    client = McpClient("https://mcp.test/x")
    answers = iter([Reply(body={"jsonrpc": "2.0", "id": 1, "result": {}}, headers={"mcp-session-id": "S"}), Reply(202),
                    Reply(body=tool_result("java.lang.NullPointerException", error=True)),
                    Reply(body={"jsonrpc": "2.0", "id": 3, "error": {"code": -32601, "message": "no such tool"}})])
    with mock.patch.object(client._http, "post", side_effect=lambda *a, **k: next(answers)):
        with pytest.raises(McpError) as first:
            client.call("t")
        with pytest.raises(McpError) as second:
            client.call("t")
    assert "NullPointer" in str(first.value) and "no such tool" in str(second.value)


# ── inside the app: ordering, fallback and the new endpoints ───────
def app_with(*names):
    return TestClient(create_app(Settings(api_keys={"k-secret": "acme"}, enable_web_ui=False, providers=list(names))))


def test_nse_first_then_tradingview_fills_what_nse_cannot_answer():
    index_quote = {"symbol": "NSE:NIFTY", "status": "ok", "price": 22421.95, "freshness": "15 min delayed"}
    nse_quote = {"symbol": "NSE:TCS", "status": "ok", "price": 2075.0, "freshness": "market closed"}

    def nse_quotes(self, symbols):
        return [nse_quote] if "NSE:TCS" in symbols else [], [s for s in symbols if s != "NSE:TCS"]

    with app_with("nse", "tradingview") as c, mock.patch.object(NseProvider, "quotes", nse_quotes), \
            mock.patch.object(svc, "snapshot_quotes", return_value=([index_quote], [])):
        body = c.get("/v1/quotes?symbols=NSE:TCS,NSE:NIFTY", headers=H).json()
    assert [q["symbol"] for q in body["data"]] == ["NSE:TCS", "NSE:NIFTY"] and body["meta"]["sources"] == ["nse", "tradingview"]


def test_a_slow_or_failing_nse_never_breaks_the_request():
    with app_with("nse", "tradingview") as c, mock.patch.object(NseProvider, "quotes", side_effect=requests.Timeout("slow")), \
            mock.patch.object(svc, "snapshot_quotes", return_value=([{"symbol": "NSE:TCS", "price": 1.0}], [])):
        body = c.get("/v1/quotes?symbols=NSE:TCS", headers=H).json()
    assert body["meta"]["sources"] == ["tradingview"]


def test_new_endpoints_answer_from_nse_and_explain_when_it_is_not_enabled():
    actions = [{"ex_date": "2026-06-05", "type": "DIVIDEND", "purpose": "x", "adjustment_factor": 1.0}]
    breadth = {"date": "2026-10-01", "advances": 883, "declines": 2714}
    with app_with("nse", "tradingview") as c, mock.patch.object(NseProvider, "corporate_actions", return_value=actions), \
            mock.patch.object(NseProvider, "market_breadth", return_value=breadth):
        a = c.get("/v1/symbols/NSE/TCS/corporate-actions?from=2026-01-01", headers=H).json()
        b = c.get("/v1/markets/breadth", headers=H).json()
    assert a["data"] == actions and a["meta"]["source"] == "nse" and a["meta"]["from"] == "2026-01-01"
    assert b["data"]["advances"] == 883 and b["meta"]["source"] == "nse"
    with app_with("tradingview") as c:
        r = c.get("/v1/markets/breadth", headers=H)
    assert r.status_code == 501 and "PROVIDERS" in r.json()["error"]["hint"]


def test_nse_is_listed_in_status_with_its_capabilities():
    with app_with("nse", "tradingview") as c:
        sources = c.get("/v1/status", headers=H).json()["data"]["sources"]
    nse = next(s for s in sources if s["name"] == "nse")
    assert nse["exchanges"] == ["NSE"] and {"quotes", "candles", "movers", "corporate_actions", "market_breadth"} == set(nse["capabilities"])
