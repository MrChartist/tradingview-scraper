"""Price-action strategies and a candle-by-candle simulator.

One engine serves both uses, so a live paper test behaves exactly like its backtest:

* `run_backtest(candles, ...)` feeds every candle of a history through the simulator.
* A paper run keeps the simulator's small state between visits and feeds only newly closed candles.

Rules, kept simple and honest:
* A signal is read at a candle's close; the trade enters at the NEXT candle's open (no peeking ahead).
* One trade at a time, long only. Stop below the pattern, target at `rr` times the risk.
* If one candle touches both stop and target, the stop is assumed first (the pessimistic reading).
* `cost_pct` is the round-trip cost (brokerage, taxes, slippage) taken off every trade.
* Nothing here sends an order. These are paper trades.
"""
import datetime
from typing import Callable, Dict, List, Optional

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
SESSION_OPEN_MIN = 9 * 60 + 15
SESSION_CLOSE_MIN = 15 * 60 + 30

TIMEFRAME_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400,
                     "1d": 86400, "1w": 604800, "1M": 2592000}

Candle = Dict[str, float]


def _ist(t: int) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(t, IST)


def _minute_of_day(t: int) -> int:
    d = _ist(t)
    return d.hour * 60 + d.minute


# ── strategies: each returns {"stop": price, "note": text} when bar i is a signal, else None ─────────────────
def breakout(c: List[Candle], i: int, *, n: int = 20, vol_mult: float = 1.5, **_) -> Optional[dict]:
    """Close above the highest high of the last n candles, on above-average volume."""
    if i < n:
        return None
    level = max(x["high"] for x in c[i - n:i])
    if c[i]["close"] <= level:
        return None
    vols = [x.get("volume") or 0 for x in c[i - n:i]]
    avg = sum(vols) / n
    if avg > 0 and (c[i].get("volume") or 0) < vol_mult * avg:
        return None
    stop = min(x["low"] for x in c[max(0, i - 2):i + 1])
    return {"stop": stop, "note": f"closed above {n}-candle high {level:.2f}"}


def retest(c: List[Candle], i: int, *, n: int = 20, window: int = 10, tol_pct: float = 0.5, **_) -> Optional[dict]:
    """Breakout above an n-candle high, pullback to that level, then a bullish candle that holds it."""
    if i < n + 2:
        return None
    tol = tol_pct / 100.0
    bar = c[i]
    if bar["close"] <= bar["open"]:
        return None
    for j in range(max(n, i - window), i):
        level = max(x["high"] for x in c[j - n:j])
        if c[j]["close"] <= level:
            continue
        since = c[j:i + 1]
        if any(x["close"] < level * (1 - tol) for x in since):
            continue                         # level failed
        if bar["low"] <= level * (1 + tol) and bar["close"] > level:
            return {"stop": min(bar["low"], level * (1 - tol)), "note": f"held retest of {level:.2f}"}
    return None


def orb(c: List[Candle], i: int, *, minutes: int = 30, last_entry: str = "14:30", **_) -> Optional[dict]:
    """Opening range breakout (India time). First close above the range high after the range is complete."""
    bar = c[i]
    mod = _minute_of_day(bar["time"])
    end_of_range = SESSION_OPEN_MIN + minutes
    h, m = (int(x) for x in last_entry.split(":"))
    if mod < end_of_range or mod > h * 60 + m:
        return None
    day = _ist(bar["time"]).date()
    today = [x for x in c[max(0, i - 400):i + 1] if _ist(x["time"]).date() == day]
    rng = [x for x in today if _minute_of_day(x["time"]) < end_of_range]
    if not rng or _minute_of_day(rng[0]["time"]) > SESSION_OPEN_MIN + 5:
        return None                          # day does not start at the open (partial data)
    high, low = max(x["high"] for x in rng), min(x["low"] for x in rng)
    if bar["close"] <= high:
        return None
    earlier = [x for x in today if _minute_of_day(x["time"]) >= end_of_range and x["time"] < bar["time"]]
    if any(x["close"] > high for x in earlier):
        return None                          # only the first breakout of the day
    return {"stop": low, "note": f"broke {minutes}-minute opening range high {high:.2f}"}


def candle_pattern(c: List[Candle], i: int, *, n: int = 10, tol_pct: float = 0.5, **_) -> Optional[dict]:
    """Bullish engulfing, hammer or inside-bar break, but only when the low sits at support (n-candle low)."""
    if i < n + 2:
        return None
    tol = tol_pct / 100.0
    b, p = c[i], c[i - 1]
    support = min(x["low"] for x in c[i - n - 1:i - 1])
    body = abs(b["close"] - b["open"])
    rng = b["high"] - b["low"]
    name = None
    if p["close"] < p["open"] and b["close"] > b["open"] and b["close"] >= p["open"] and b["open"] <= p["close"]:
        name, low = "bullish engulfing", min(b["low"], p["low"])
    elif rng > 0 and b["close"] > b["open"] and (min(b["open"], b["close"]) - b["low"]) >= 2 * max(body, rng * 0.05) \
            and (b["high"] - max(b["open"], b["close"])) <= body:
        name, low = "hammer", b["low"]
    else:
        m = c[i - 2]
        if p["high"] < m["high"] and p["low"] > m["low"] and b["close"] > m["high"]:
            name, low = "inside-bar break", m["low"]
    if not name or low > support * (1 + tol):
        return None
    return {"stop": low, "note": f"{name} at {n}-candle support {support:.2f}"}


STRATEGIES: Dict[str, dict] = {
    "breakout": {"fn": breakout, "intraday_only": False,
                 "summary": "Close above the highest high of the last N candles, with volume above average.",
                 "defaults": {"n": 20, "vol_mult": 1.5}},
    "retest": {"fn": retest, "intraday_only": False,
               "summary": "Breakout, pullback to the broken level, then a bullish candle that holds it.",
               "defaults": {"n": 20, "window": 10, "tol_pct": 0.5}},
    "orb": {"fn": orb, "intraday_only": True,
            "summary": "Opening range breakout: first close above the first N minutes' high (India time). Intraday only.",
            "defaults": {"minutes": 30, "last_entry": "14:30"}},
    "candle": {"fn": candle_pattern, "intraday_only": False,
               "summary": "Engulfing, hammer or inside-bar break, only at support (N-candle low).",
               "defaults": {"n": 10, "tol_pct": 0.5}},
}


# ── simulator ───────────────────────────────────────────────────────────────────────────────────────────────
def new_state() -> dict:
    return {"pending": None, "position": None, "last_time": None, "trades": []}


def _close_trade(state: dict, pos: dict, price: float, time: int, reason: str, cost_pct: float) -> None:
    ret = (price / pos["entry"] - 1) * 100 - cost_pct
    risk_pct = (pos["entry"] - pos["stop"]) / pos["entry"] * 100
    state["trades"].append({
        "entry_time": pos["entry_time"], "entry": round(pos["entry"], 4), "stop": round(pos["stop"], 4),
        "target": round(pos["target"], 4), "exit_time": time, "exit": round(price, 4), "reason": reason,
        "return_pct": round(ret, 3), "r": round(ret / risk_pct, 2) if risk_pct else 0.0,
        "bars": pos["bars"], "note": pos["note"],
    })
    state["position"] = None


def feed(state: dict, c: List[Candle], i: int, strategy: str, params: dict, *, rr: float, cost_pct: float,
         max_hold: int, tf_seconds: int) -> None:
    """Advance the simulation by one closed candle: c[i]. Needs c[:i+1] for context."""
    bar = c[i]
    if state["last_time"] is not None and bar["time"] <= state["last_time"]:
        return
    state["last_time"] = bar["time"]

    if state["pending"] and state["position"] is None:      # enter at this candle's open
        sig, state["pending"] = state["pending"], None
        entry = bar["open"]
        risk = entry - sig["stop"]
        if risk > 0:
            state["position"] = {"entry": entry, "stop": sig["stop"], "target": entry + rr * risk,
                                 "entry_time": bar["time"], "bars": 0, "note": sig["note"]}
    pos = state["position"]
    if pos:
        pos["bars"] += 1
        if bar["open"] <= pos["stop"]:
            _close_trade(state, pos, bar["open"], bar["time"], "stop (gap)", cost_pct)
        elif bar["low"] <= pos["stop"]:
            _close_trade(state, pos, pos["stop"], bar["time"], "stop", cost_pct)
        elif bar["open"] >= pos["target"]:
            _close_trade(state, pos, bar["open"], bar["time"], "target (gap)", cost_pct)
        elif bar["high"] >= pos["target"]:
            _close_trade(state, pos, pos["target"], bar["time"], "target", cost_pct)
        elif tf_seconds < 86400 and _minute_of_day(bar["time"]) + tf_seconds // 60 >= SESSION_CLOSE_MIN:
            _close_trade(state, pos, bar["close"], bar["time"], "end of day", cost_pct)
        elif pos["bars"] >= max_hold:
            _close_trade(state, pos, bar["close"], bar["time"], "time limit", cost_pct)
    if state["position"] is None and state["pending"] is None:
        fn: Callable = STRATEGIES[strategy]["fn"]
        sig = fn(c, i, **params)
        if sig and bar["close"] > sig["stop"]:
            state["pending"] = sig


def summarize(trades: List[dict], candles: Optional[List[Candle]] = None) -> dict:
    n = len(trades)
    out = {"trades": n, "wins": 0, "losses": 0, "win_rate_pct": None, "avg_return_pct": None,
           "expectancy_r": None, "profit_factor": None, "total_return_pct": 0.0, "max_drawdown_pct": 0.0,
           "best_pct": None, "worst_pct": None}
    if candles and len(candles) > 1:
        out["buy_and_hold_pct"] = round((candles[-1]["close"] / candles[0]["close"] - 1) * 100, 2)
    if not n:
        return out
    rets = [t["return_pct"] for t in trades]
    wins, losses = [r for r in rets if r > 0], [r for r in rets if r <= 0]
    equity, peak, dd = 1.0, 1.0, 0.0
    for r in rets:
        equity *= 1 + r / 100
        peak = max(peak, equity)
        dd = max(dd, (peak - equity) / peak * 100)
    out.update({
        "wins": len(wins), "losses": len(losses), "win_rate_pct": round(len(wins) / n * 100, 1),
        "avg_return_pct": round(sum(rets) / n, 3), "expectancy_r": round(sum(t["r"] for t in trades) / n, 2),
        "profit_factor": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None,
        "total_return_pct": round((equity - 1) * 100, 2), "max_drawdown_pct": round(dd, 2),
        "best_pct": round(max(rets), 2), "worst_pct": round(min(rets), 2),
        "avg_bars": round(sum(t["bars"] for t in trades) / n, 1),
    })
    return out


_SPLIT_RATIOS = (2, 3, 4, 5, 10, 1.5, 2.5, 20)


def adjust_for_splits(candles: List[Candle]) -> tuple:
    """Some sources give prices as traded, so a 1:1 bonus or a split shows up as a fake crash.

    India limits a day's move to 20% for most shares, so a gap of 38% or more between two closes almost surely
    means a split or bonus. When the drop matches a simple ratio (1:2, 1:5, 1:10, ...), older candles are scaled
    to today's basis. Returns (new candles, list of what was changed) so the caller can say so."""
    rows = [dict(c) for c in candles]
    events = []
    for i in range(len(rows) - 1, 0, -1):
        prev, now = rows[i - 1]["close"], rows[i]["open"]
        if not prev or not now:
            continue
        r = now / prev
        for k in _SPLIT_RATIOS:
            if r < 0.62 and abs(r - 1 / k) / (1 / k) < 0.08:
                for row in rows[:i]:
                    for f in ("open", "high", "low", "close"):
                        row[f] = row[f] / k
                    row["volume"] = (row.get("volume") or 0) * k
                events.append({"time": rows[i]["time"], "ratio": f"1:{k:g}"})
                break
    return rows, events


def merged_params(strategy: str, params: Optional[dict]) -> dict:
    spec = STRATEGIES[strategy]
    unknown = set(params or {}) - set(spec["defaults"])
    if unknown:
        raise ValueError(f"Unknown setting for '{strategy}': {', '.join(sorted(unknown))}. "
                         f"Allowed: {', '.join(spec['defaults'])}.")
    return {**spec["defaults"], **(params or {})}


def run_backtest(candles: List[Candle], strategy: str, params: Optional[dict] = None, *, rr: float = 2.0,
                 cost_pct: float = 0.1, max_hold: int = 30, tf_seconds: int = 86400) -> dict:
    p = merged_params(strategy, params)
    state = new_state()
    for i in range(len(candles)):
        feed(state, candles, i, strategy, p, rr=rr, cost_pct=cost_pct, max_hold=max_hold, tf_seconds=tf_seconds)
    return {"params": p, "trades": state["trades"], "open_position": state["position"], "pending_signal": state["pending"],
            "stats": summarize(state["trades"], candles)}
