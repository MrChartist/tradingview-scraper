"""Backtest engine, paper forward-test book, and the operations around them (all offline)."""
import datetime
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from api import backtest as bt
from api import operations as ops
from api import services as svc
from api.config import Settings
from api.main import create_app
from api.paper import PaperBook, candle_is_closed
from tests.test_v1 import H, KEY

IST = bt.IST
DAY = 86400


def bar(t, o, h, l, c, v=1000):
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v}


def flat(n, price=100.0, t0=1_700_000_000):
    return [bar(t0 + i * DAY, price, price + 1, price - 1, price) for i in range(n)]


def breakout_series():
    c = flat(25)
    t = c[-1]["time"]
    c.append(bar(t + DAY, 100, 106, 99.5, 105, v=5000))        # breakout candle: signal at its close
    return c, t + 2 * DAY


def test_enters_at_next_open_not_at_signal_close():
    c, t = breakout_series()
    c.append(bar(t, 105.5, 106, 105, 105.5))
    out = bt.run_backtest(c, "breakout", {"n": 20}, rr=2)
    pos = out["open_position"]
    assert pos and pos["entry"] == 105.5 and pos["entry_time"] == t


def test_target_hit_gives_positive_trade():
    c, t = breakout_series()
    c += [bar(t, 105.5, 106, 105, 105.5), bar(t + DAY, 106, 120, 105.5, 119)]
    out = bt.run_backtest(c, "breakout", {"n": 20}, rr=2, cost_pct=0)
    trade = out["trades"][0]
    assert trade["reason"] == "target" and trade["return_pct"] > 0 and trade["r"] == pytest.approx(2, abs=0.1)


def test_stop_wins_when_one_candle_touches_both():
    c, t = breakout_series()
    c += [bar(t, 105.5, 106, 105, 105.5), bar(t + DAY, 106, 130, 90, 100)]
    trade = bt.run_backtest(c, "breakout", {"n": 20}, rr=2, cost_pct=0)["trades"][0]
    assert trade["reason"] == "stop" and trade["return_pct"] < 0


def test_gap_down_through_stop_exits_at_open():
    c, t = breakout_series()
    c += [bar(t, 105.5, 106, 105, 105.5), bar(t + DAY, 90, 91, 88, 90)]
    trade = bt.run_backtest(c, "breakout", {"n": 20}, rr=2, cost_pct=0)["trades"][0]
    assert trade["reason"] == "stop (gap)" and trade["exit"] == 90


def test_volume_filter_blocks_weak_breakout():
    c = flat(25)
    c.append(bar(c[-1]["time"] + DAY, 100, 106, 99.5, 105, v=1000))
    out = bt.run_backtest(c, "breakout", {"n": 20, "vol_mult": 1.5})
    assert out["pending_signal"] is None and out["stats"]["trades"] == 0


def test_cost_is_taken_off_every_trade():
    c, t = breakout_series()
    c += [bar(t, 105.5, 106, 105, 105.5), bar(t + DAY, 106, 120, 105.5, 119)]
    a = bt.run_backtest(c, "breakout", {"n": 20}, cost_pct=0)["trades"][0]["return_pct"]
    b = bt.run_backtest(c, "breakout", {"n": 20}, cost_pct=0.5)["trades"][0]["return_pct"]
    assert a - b == pytest.approx(0.5, abs=0.01)


def test_time_limit_exit():
    c, t = breakout_series()
    c += [bar(t, 105.5, 106, 105, 105.5)] + [bar(t + (i + 1) * DAY, 105.5, 106, 105, 105.5) for i in range(5)]
    trade = bt.run_backtest(c, "breakout", {"n": 20}, max_hold=3)["trades"][0]
    assert trade["reason"] == "time limit" and trade["bars"] == 3


def test_retest_needs_pullback_and_hold():
    c = flat(25)
    t = c[-1]["time"]
    c.append(bar(t + DAY, 100, 106, 100, 105.5, v=5000))            # breakout above 101
    c.append(bar(t + 2 * DAY, 105, 107, 104, 106))                  # runs away: no retest yet
    c.append(bar(t + 3 * DAY, 104, 104.5, 101.2, 103.5))             # pulls back to 101.2 (touch), bearish
    c.append(bar(t + 4 * DAY, 101.3, 104, 101.1, 103.8))             # holds 101, bullish close above level
    out = bt.run_backtest(c, "retest", {"n": 20, "window": 10, "tol_pct": 0.5})
    assert out["pending_signal"] and "retest" in out["pending_signal"]["note"]


def test_candle_pattern_only_at_support():
    c = flat(20)
    t = c[-1]["time"]
    c.append(bar(t + DAY, 100.5, 100.8, 98, 98.5))                  # red candle, new low
    c.append(bar(t + 2 * DAY, 98.2, 101.5, 98, 101.0))              # bullish engulfing at support
    out = bt.run_backtest(c, "candle", {"n": 10})
    assert out["pending_signal"] and "engulfing" in out["pending_signal"]["note"]
    far = flat(20, 100) + [bar(t + DAY, 108, 109, 107.5, 108.6), bar(t + 2 * DAY, 108.4, 111, 108.3, 110.9)]
    far[-2], far[-1] = bar(t + DAY, 110, 110.5, 107.5, 108), bar(t + 2 * DAY, 107.8, 111, 107.5, 110.8)
    assert bt.run_backtest(far, "candle", {"n": 10})["pending_signal"] is None


def _ist(y, m, d, hh, mm):
    return int(datetime.datetime(y, m, d, hh, mm, tzinfo=IST).timestamp())


def test_orb_enters_first_break_and_exits_at_day_end():
    day = (2026, 9, 29)
    t0 = _ist(*day, 9, 15)
    c = [bar(t0 + i * 900, 100, 101, 99, 100) for i in range(2)]              # 09:15 and 09:30 form the range (30 min)
    c += [bar(t0 + 2 * 900, 100, 103, 100, 102.5), bar(t0 + 3 * 900, 102.6, 103, 102.0, 102.5)]
    c += [bar(t0 + i * 900, 102.5, 102.8, 102.2, 102.5) for i in range(4, 25)]  # drifts to the close, no stop/target
    out = bt.run_backtest(c, "orb", {"minutes": 30}, rr=50, tf_seconds=900)
    assert out["trades"] and out["trades"][0]["reason"] == "end of day"
    with pytest.raises(ValueError):
        bt.merged_params("orb", {"nonsense": 1})


def test_summary_numbers():
    trades = [{"return_pct": 4.0, "r": 2, "bars": 3}, {"return_pct": -2.0, "r": -1, "bars": 2}, {"return_pct": 4.0, "r": 2, "bars": 5}]
    s = bt.summarize(trades)
    assert s["trades"] == 3 and s["wins"] == 2 and s["win_rate_pct"] == 66.7
    assert s["profit_factor"] == 4.0 and s["total_return_pct"] == pytest.approx(5.9, abs=0.1)
    assert s["max_drawdown_pct"] == 2.0


def test_split_adjustment_removes_fake_crash():
    c = [bar(i * DAY, 200, 202, 198, 200) for i in range(10)] + [bar(i * DAY, 100, 101, 99, 100) for i in range(10, 20)]
    rows, events = bt.adjust_for_splits(c)
    assert events and events[0]["ratio"] == "1:2"
    assert rows[0]["close"] == 100 and rows[0]["volume"] == 2000 and c[0]["close"] == 200      # input untouched
    assert bt.adjust_for_splits([bar(i * DAY, 100 - i, 101, 90, 99 - i) for i in range(10)])[1] == []   # slow slide is not a split


def test_candle_closed_rules():
    now = _ist(2026, 9, 29, 12, 0)
    assert not candle_is_closed("NSE", _ist(2026, 9, 29, 9, 15), DAY, now)      # today, market still open
    assert candle_is_closed("NSE", _ist(2026, 9, 28, 9, 15), DAY, now)          # yesterday
    assert candle_is_closed("NSE", _ist(2026, 9, 29, 9, 15), DAY, _ist(2026, 9, 29, 16, 0))
    assert not candle_is_closed("BINANCE", now - 1800, 3600, now)
    assert candle_is_closed("BINANCE", now - 3600, 3600, now)


def test_paper_book_runs_forward_and_survives_restart(tmp_path):
    path = str(tmp_path / "paper.json")
    book = PaperBook(path)
    run = book.start("acme", "BINANCE:BTCUSDT", "1h", "breakout", bt.merged_params("breakout", {}), rr=2, cost_pct=0, max_hold=30)
    now = (int(datetime.datetime.now().timestamp()) // 3600) * 3600
    history = [bar(now - (40 - i) * 3600, 100, 101, 99, 100) for i in range(30)]
    book.tick(lambda s, tf, n: history)
    assert book.get("acme", run["id"])["started"] and not book.get("acme", run["id"])["state"]["trades"]
    later = history + [bar(now - 9 * 3600, 100, 106, 99.5, 105, v=5000), bar(now - 8 * 3600, 105.5, 106, 105, 105.5),
                       bar(now - 7 * 3600, 106, 125, 105.5, 124)]
    book.tick(lambda s, tf, n: later)
    view = PaperBook(path).view(PaperBook(path).get("acme", run["id"]))          # a fresh book reads the file
    assert view["stats"]["trades"] == 1 and view["trades"][0]["reason"] == "target"
    with pytest.raises(Exception):
        book.get("someone-else", run["id"])


def test_paper_run_error_does_not_stop_others(tmp_path):
    book = PaperBook(str(tmp_path / "p.json"))
    a = book.start("x", "NSE:AAA", "1d", "breakout", {"n": 20, "vol_mult": 1.5}, rr=2, cost_pct=0, max_hold=5)
    b = book.start("x", "NSE:BBB", "1d", "breakout", {"n": 20, "vol_mult": 1.5}, rr=2, cost_pct=0, max_hold=5)

    def fetch(sym, tf, n):
        if sym.endswith("AAA"):
            raise RuntimeError("upstream down")
        return flat(30, t0=1_600_000_000)

    assert book.tick(fetch) == 1
    assert "upstream down" in book.get("x", a["id"])["error"] and book.get("x", b["id"])["error"] is None


def test_paper_limit_per_owner(tmp_path):
    book = PaperBook(str(tmp_path / "p.json"), max_runs_per_owner=1)
    book.start("x", "NSE:AAA", "1d", "breakout", {}, rr=2, cost_pct=0, max_hold=5)
    with pytest.raises(Exception) as e:
        book.start("x", "NSE:BBB", "1d", "breakout", {}, rr=2, cost_pct=0, max_hold=5)
    assert getattr(e.value, "status", getattr(e.value, "status_code", 429)) == 429


def rows(n=300):
    t0 = 1_600_000_000
    return [{"time": t0 + i * DAY, "datetime": "x", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000} for i in range(n)]


def client(tmp_path, poll=60):
    return TestClient(create_app(Settings(api_keys=KEY, rate_limit_per_minute=100, enable_web_ui=False,
                                          paper_file=str(tmp_path / "paper.json"), paper_poll_seconds=poll)))


def test_rest_routes(tmp_path):
    with client(tmp_path) as c, mock.patch.object(ops, "_candle_rows", return_value=rows()):
        r = c.post("/v1/backtest", headers=H, json={"symbol": "NSE:TCS", "strategy": "candle"})
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["stats"]["trades"] == 0 and r.json()["meta"]["caution"]
        assert c.post("/v1/backtest", headers=H, json={"symbol": "NSE:TCS", "strategy": "orb"}).status_code == 400
        assert c.post("/v1/backtest", headers=H, json={"symbol": "NSE:TCS", "settings": {"nope": 1}}).status_code == 422
        run = c.post("/v1/paper", headers=H, json={"symbol": "NSE:TCS", "strategy": "breakout"}).json()["data"]
        assert c.get("/v1/paper", headers=H).json()["meta"]["count"] == 1
        assert c.get(f"/v1/paper/{run['id']}", headers=H).json()["data"]["status"] == "running"
        assert c.delete(f"/v1/paper/{run['id']}?delete=true", headers=H).json()["data"]["status"] == "stopped"
        assert c.get("/v1/paper", headers=H).json()["meta"]["count"] == 0


def test_paper_off_when_poll_is_zero(tmp_path):
    with client(tmp_path, poll=0) as c:
        assert c.get("/v1/paper", headers=H).status_code == 501


def test_paper_ops_through_context(tmp_path):
    book = PaperBook(str(tmp_path / "p.json"))
    ctx = ops.Context(settings=Settings(), owner="acme", paper=book)
    with mock.patch.object(ops, "_candle_rows", return_value=rows()):
        started = ops.execute("paper_start", {"symbol": "NSE:TCS", "strategy": "retest"}, ctx).data
    assert started["status"] == "running"
    assert ops.execute("paper_list", {}, ctx).meta["count"] == 1
    assert ops.execute("paper_get", {"id": started["id"]}, ctx).data["symbol"] == "NSE:TCS"
    assert ops.execute("paper_stop", {"id": started["id"]}, ctx).data["status"] == "stopped"
    other = ops.Context(settings=Settings(), owner="other", paper=book)
    with pytest.raises(ops.ApiError):
        ops.execute("paper_get", {"id": started["id"]}, other)
    with pytest.raises(ops.ApiError) as e:
        ops.execute("paper_list", {}, ops.Context(settings=Settings()))
    assert e.value.status == 501
