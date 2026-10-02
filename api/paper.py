"""Paper forward-tests: a strategy that keeps running on live candles and records pretend trades.

Nothing here places an order. A run is saved in a small JSON file, so it survives restarts. A background
loop (started with the app) visits every running test, fetches candles, and feeds only the candles that
have newly CLOSED into the same simulator the backtest uses.
"""
import asyncio
import datetime
import json
import logging
import os
import tempfile
import threading
import time
import uuid
from typing import Dict, List, Optional

from . import backtest as bt
from . import markets_india as india
from .errors import ApiError

log = logging.getLogger("market_terminal.paper")
WARMUP_CANDLES = 300
MAX_TRADES_KEPT = 500


def candle_is_closed(exchange: str, bar_time: int, tf_seconds: int, now: Optional[float] = None) -> bool:
    now = now if now is not None else time.time()
    if tf_seconds >= 86400 and exchange.upper() in ("NSE", "BSE"):
        # Daily candle for India is final once the market has closed (or the day is over).
        at = datetime.datetime.fromtimestamp(now, india.IST)
        day = datetime.datetime.fromtimestamp(bar_time, india.IST).date()
        return day < at.date() or (day == at.date() and at.time() >= india.CLOSE)
    return bar_time + tf_seconds <= now


class PaperBook:
    def __init__(self, path: str, max_runs_per_owner: int = 20):
        self.path = path
        self.max_runs = max_runs_per_owner
        self._lock = threading.RLock()
        self.runs: Dict[str, dict] = {}
        self._load()

    # ── storage ──────────────────────────────────────────────────
    def _load(self) -> None:
        try:
            with open(self.path) as f:
                self.runs = {r["id"]: r for r in json.load(f).get("runs", [])}
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log.warning("Could not read %s (%s). Starting with no paper tests.", self.path, e)

    def _save(self) -> None:
        folder = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump({"runs": list(self.runs.values())}, f)
        os.replace(tmp, self.path)

    # ── api ──────────────────────────────────────────────────────
    def start(self, owner: str, symbol: str, timeframe: str, strategy: str, params: dict, *, rr: float,
              cost_pct: float, max_hold: int) -> dict:
        with self._lock:
            mine = [r for r in self.runs.values() if r["owner"] == owner and r["status"] == "running"]
            if len(mine) >= self.max_runs:
                raise ApiError(429, f"At most {self.max_runs} running paper tests per client.",
                               hint="Stop one with paper_stop, then start again.")
            run = {"id": uuid.uuid4().hex[:10], "owner": owner, "symbol": symbol, "timeframe": timeframe,
                   "strategy": strategy, "params": params, "rr": rr, "cost_pct": cost_pct, "max_hold": max_hold,
                   "status": "running", "created": int(time.time()), "last_checked": None, "error": None,
                   "state": bt.new_state(), "started": False}
            self.runs[run["id"]] = run
            self._save()
            return self.view(run)

    def get(self, owner: str, run_id: str) -> dict:
        with self._lock:
            run = self.runs.get(run_id)
            if not run or run["owner"] != owner:
                raise ApiError(404, f"No paper test with id '{run_id}'.", hint="List yours with paper_list.")
            return run

    def list(self, owner: str) -> List[dict]:
        with self._lock:
            return [self.view(r, brief=True) for r in self.runs.values() if r["owner"] == owner]

    def stop(self, owner: str, run_id: str, delete: bool = False) -> dict:
        with self._lock:
            run = self.get(owner, run_id)
            run["status"] = "stopped"
            view = self.view(run)
            if delete:
                del self.runs[run_id]
            self._save()
            return view

    @staticmethod
    def view(run: dict, brief: bool = False) -> dict:
        st = run["state"]
        out = {k: run[k] for k in ("id", "symbol", "timeframe", "strategy", "params", "rr", "cost_pct", "max_hold",
                                    "status", "created", "last_checked", "error")}
        out["stats"] = bt.summarize(st["trades"])
        out["open_position"] = st["position"]
        out["waiting_to_enter"] = st["pending"]
        if not brief:
            out["trades"] = st["trades"][-100:]
        out["note"] = "Paper trades only. No orders are placed. Past results do not predict future results."
        return out

    # ── one visit ────────────────────────────────────────────────
    def tick_run(self, run: dict, fetch) -> None:
        """fetch(symbol, timeframe, limit) -> list of candle dicts, oldest first."""
        exchange = run["symbol"].split(":")[0]
        tf = bt.TIMEFRAME_SECONDS[run["timeframe"]]
        candles = fetch(run["symbol"], run["timeframe"], WARMUP_CANDLES)
        closed = [c for c in candles if candle_is_closed(exchange, c["time"], tf)]
        with self._lock:
            run["last_checked"] = int(time.time())
            run["error"] = None
            if not closed:
                return
            st = run["state"]
            if not run["started"]:
                run["started"] = True          # begin from now: history is only warm-up, not replayed
                st["last_time"] = closed[-1]["time"]
                return
            for i, bar in enumerate(closed):
                if bar["time"] > st["last_time"]:
                    bt.feed(st, closed, i, run["strategy"], run["params"], rr=run["rr"], cost_pct=run["cost_pct"],
                            max_hold=run["max_hold"], tf_seconds=tf)
            del st["trades"][:-MAX_TRADES_KEPT]

    def tick(self, fetch) -> int:
        with self._lock:
            active = [r for r in self.runs.values() if r["status"] == "running"]
        done = 0
        for run in active:
            try:
                self.tick_run(run, fetch)
                done += 1
            except Exception as e:  # one broken run must never stop the others
                with self._lock:
                    run["error"] = f"{type(e).__name__}: {e}"[:200]
                    run["last_checked"] = int(time.time())
                log.warning("paper run %s: %s", run["id"], e)
        if active:
            with self._lock:
                self._save()
        return done


async def run_forever(book: PaperBook, fetch, interval: int) -> None:
    from starlette.concurrency import run_in_threadpool
    while True:
        try:
            await run_in_threadpool(book.tick, fetch)
        except Exception as e:  # pragma: no cover
            log.warning("paper loop: %s", e)
        await asyncio.sleep(interval)
