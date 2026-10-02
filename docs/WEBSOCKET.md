# The Tickvale WebSocket

One connection does everything: ask for data, get one answer; subscribe, get live updates. It is the main way to use Tickvale. The REST endpoints (see [API.md](API.md)) are the same operations over plain HTTP.

```text
wss://your-host/v1/ws
```

Send your key as the `X-API-Key` header, as `Authorization: Bearer <key>`, or as `?api_key=<key>` (browsers cannot set headers on a WebSocket, so they use the query form). Without configured keys the server is open and needs none.

## What happens when you connect

The server speaks first with a **`hello`** that describes itself, so you never need to guess:

```json
{
  "type": "hello", "service": "tickvale", "version": "3.0.0", "auth": "api_key", "client": "acme",
  "operations": [{"name": "quotes", "summary": "Latest quote for up to 100 symbols (snapshot).", "public": false, "params": ["symbols"]}, "..."],
  "channels": [{"name": "quotes", "summary": "Live prices. ...", "params": "symbols: list of symbols or names"}, "..."],
  "limits": {"max_symbols": 100, "max_in_flight": 8, "max_message_bytes": 65536, "requests_per_minute": 120},
  "how_to": {"ask": {"id": "1", "op": "quotes", "params": {"symbols": ["reliance", "bitcoin"]}},
             "subscribe": {"op": "subscribe", "params": {"channel": "quotes", "symbols": ["NSE:TCS"]}}},
  "note": "Free stock prices are usually 15 minutes delayed. Check each quote's 'freshness'."
}
```

## Asking

Send `{"id": "<anything>", "op": "<operation>", "params": {...}}`. You get back exactly one frame with the same `id`:

```json
{"type": "result", "id": "7", "op": "quotes", "data": [ ... ], "meta": {"count": 2, "not_found": []}}
{"type": "error",  "id": "7", "code": "not_found", "message": "Nothing matched 'zzzz'.", "hint": "Try a shorter or different spelling...", "status": 404}
```

* Requests run side by side (up to 8 at a time per connection); answers can come back in any order. Match them by `id`.
* An error never closes the connection.
* **Plain names work.** `{"symbols": ["reliance", "bitcoin"]}` is resolved to `NSE:RELIANCE` and `BINANCE:BTCUSDT`.
* Unknown parameters are rejected rather than ignored, so typos get caught.

### Operations

| `op` | Params | Answer |
|---|---|---|
| `quotes` | `symbols` (list or comma text, up to 100) | Latest quote for each; `meta.not_found` lists unknown ones |
| `resolve` | `q`, optional `country` | Best `EXCHANGE:TICKER` plus alternatives |
| `search` | `q`, `limit` | Matching symbols |
| `symbol` | `symbol` | Profile and key statistics |
| `fundamentals` | `symbol` | Company numbers |
| `technicals` | `symbol`, `timeframe` | Advanced indicator readings |
| `candles` | `symbol`, `timeframe`, `limit` | OHLCV, oldest first |
| `news` | `symbol`, `limit`, `language` | Headlines |
| `movers` | `market`, `category`, `limit` | Gainers, losers, most active |
| `screener` | `market`, `conditions`, `columns`, `sort_by`, `sort_order`, `limit`, `main_only` | Stocks matching your rules |
| `earnings`, `dividends` | `markets`, `from`, `to`, `limit` | Calendar events |
| `backtest` | `symbol`, `strategy`, `timeframe`, `settings`, `rr`, `cost_pct`, `max_hold`, `limit` | Test a rule on past candles |
| `paper_start`, `paper_list`, `paper_get`, `paper_stop` | `symbol`, `strategy`, ... / `id` | Paper tests that keep running |
| `corporate_actions` | `symbol` | Dividends, splits, bonus (needs the `nse` source) |
| `market_breadth` | `date` (optional) | Advances / declines (needs the `nse` source) |
| `markets`, `glossary` | none | Valid values and plain-language terms |
| `status` | none | Live-feed health and limits |
| `ping` | none | `pong` (free, not rate limited) |

The `hello` message always carries the current list, including any added by plugins.

## Subscribing

```json
{"id": "s1", "op": "subscribe", "params": {"channel": "quotes", "symbols": ["bitcoin", "NSE:TCS", "zzzz"]}}
```
```json
{"type": "subscribed", "id": "s1", "channel": "quotes",
 "symbols": ["BINANCE:BTCUSDT", "NSE:TCS"],
 "resolved": {"bitcoin": "BINANCE:BTCUSDT"},
 "rejected": [{"symbol": "ZZZZ", "reason": "not_found"}]}
```

Then pushes arrive until you unsubscribe or disconnect.

| Channel | Params | Pushes |
|---|---|---|
| `quotes` | `symbols` | `{"type": "quote", "data": {...}}` for each price change |
| `movers` | `market`, `category`, `limit`, `interval` (15 to 300 s, default 30) | `{"type": "update", "channel": "movers", "key": "stocks-india:gainers", "data": [...]}` only when the list changes |

`{"op": "unsubscribe", "params": {"channel": "quotes", "symbols": ["NSE:TCS"]}}` stops a feed (`channel` defaults to `quotes`).

### A quote

```json
{"symbol": "NSE:RELIANCE", "status": "ok", "price": 1167.7, "change": -19.3, "change_percent": -1.63,
 "open": 1180.1, "high": 1183.9, "low": 1160.8, "volume": 16771221, "currency": "INR",
 "update_mode": "delayed_streaming_900", "freshness": "15 min delayed", "realtime": false, "delayed": true, "delay_seconds": 900}
```

A symbol that does not exist is sent once as `{"symbol": "...", "status": "error", "error": "symbol_not_found"}`.

If you are slow to read, you get the **latest** price per symbol, never a backlog.

## Other frames

| Frame | When |
|---|---|
| `{"type": "heartbeat", "time": ...}` | Every 20 seconds, so you can tell the connection is alive |
| `{"type": "pong"}` | Reply to `{"op": "ping"}` |
| `{"type": "error", "id": null, ...}` | Something not tied to one request, such as a malformed message |

## Closing and limits

| Code | Meaning |
|---|---|
| `4401` | Missing or invalid API key (an `error` frame explains first) |
| `4429` | Too many connections for this key, or rate limit hit while connecting |
| `4400` | Too many malformed messages in a row (10) |
| `1009` | A message larger than 64 KB |

Every request and subscription counts toward the per-minute limit; pings do not. Subscribe instead of polling. Live connections per key are capped (default 5, shared with server-sent events), and 100 symbols per connection.

## Try it

```bash
# websocat (https://github.com/vi/websocat)
websocat "ws://localhost:8000/v1/ws"
{"id":"1","op":"quotes","params":{"symbols":["reliance"]}}
```

Python (SDK, reconnects by itself):

```python
async with client.socket() as sock:
    print(await sock.call("quotes", symbols=["reliance", "bitcoin"]))
    await sock.subscribe("quotes", symbols=["bitcoin", "NSE:TCS"])
    await sock.subscribe("movers", market="stocks-india", category="gainers")
    async for event in sock.events():          # quote, update, connected, disconnected
        print(event["type"], event.get("data"))
```

JavaScript:

```js
const sock = client.socket();
await sock.connect();
console.log(await sock.call('quotes', { symbols: ['reliance', 'bitcoin'] }));
sock.on('quote', q => console.log(q.symbol, q.price, q.freshness));
await sock.subscribe('quotes', { symbols: ['bitcoin'] });
```

Older frames such as `{"action": "subscribe", "symbols": [...]}` still work.
