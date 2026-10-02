# Backtest and paper tests

Two things, one engine, so a paper test behaves exactly like its backtest.

* **Backtest**: run a rule over past candles and see every trade and the totals.
* **Paper test**: the same rule keeps running on new candles. Each time a candle closes it is fed to the rule and pretend trades are written down. It is saved to `PAPER_FILE` and survives a restart.

Nothing places an order. Tickvale stays read-only.

## The rules (price action only)

| `strategy` | What it does | `settings` (defaults) |
|---|---|---|
| `breakout` | Close above the highest high of the last `n` candles, with volume at least `vol_mult` times the average | `n` 20, `vol_mult` 1.5 |
| `retest` | Breakout above `n` candles, pullback within `tol_pct` of that level, then a green candle that holds it, within `window` candles | `n` 20, `window` 10, `tol_pct` 0.5 |
| `candle` | Bullish engulfing, hammer or inside-bar break, only at the `n`-candle low (support) | `n` 10, `tol_pct` 0.5 |
| `orb` | Opening range breakout (India time): first close above the first `minutes` high, entries until `last_entry`. Intraday timeframes only | `minutes` 30, `last_entry` "14:30" |

## How a trade is simulated

* The signal is read at a candle's close. The trade **enters at the next candle's open** (no peeking ahead).
* One trade at a time, long only. Stop under the pattern. Target at `rr` times the risk (default 2).
* If one candle touches both stop and target, the **stop is assumed first**. A gap through the stop exits at the open.
* A trade ends at the stop, the target, after `max_hold` candles (default 30), or at the end of the day for intraday rules.
* `cost_pct` (default 0.1) is taken off every trade for brokerage, taxes and slippage.
* Prices that fall 38% or more between two candles in a simple ratio (1:2, 1:5, 1:10 ...) are treated as a split or bonus, and older candles are scaled. This is reported in `meta.caution` and `data.adjusted_for`. Check against your chart.

## Use it

```bash
curl -X POST http://localhost:8000/v1/backtest -H 'X-API-Key: ...' -H 'content-type: application/json' \
  -d '{"symbol":"NSE:RELIANCE","strategy":"breakout","limit":1250}'

curl -X POST http://localhost:8000/v1/paper -H 'X-API-Key: ...' -H 'content-type: application/json' \
  -d '{"symbol":"NSE:NIFTY","strategy":"orb","timeframe":"15m"}'
curl http://localhost:8000/v1/paper -H 'X-API-Key: ...'            # your paper tests
curl http://localhost:8000/v1/paper/<id> -H 'X-API-Key: ...'       # one, with its trades
curl -X DELETE 'http://localhost:8000/v1/paper/<id>?delete=true' -H 'X-API-Key: ...'
```

On the WebSocket the operations are `backtest`, `paper_start`, `paper_list`, `paper_get` and `paper_stop`. Each client (key) sees only its own paper tests.

The web page has a **Test a strategy** tab. Paper tests from the page work only on a server without API keys (your own machine). On a public server use the keyed API.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `PAPER_POLL_SECONDS` | 60 | How often running paper tests are checked. `0` switches paper tests off. |
| `PAPER_FILE` | `data/paper.json` | Where they are saved. |
| `PAPER_MAX_RUNS_PER_CLIENT` | 20 | Running tests per client. |

Data comes from your sources in order. For India, `PROVIDERS=nse,tradingview` gives about five years of daily candles from NSE and intraday from TradingView.

## Read the result honestly

* Fewer than 30 trades means the numbers are too thin to trust.
* A good backtest is not a promise. Compare with `buy_and_hold_pct`, and look at `max_drawdown_pct`.
* Tune a rule on one set of candles and test it on another. A paper test is that second set.
* A paper test uses candle data, so it cannot show slippage or a fill that never came.
