# API reference (v1)

> The WebSocket is the main interface: see [WEBSOCKET.md](WEBSOCKET.md). This page covers the same operations over REST.

Base URL: wherever you deploy it, for example `https://api.example.com`. Interactive docs: `/docs`. Machine-readable contract: `/openapi.json`.

## Conventions

| | |
|---|---|
| **Auth** | `X-API-Key: <key>` or `Authorization: Bearer <key>`. WebSocket and SSE can also use `?api_key=<key>` (browsers cannot set headers there). Without configured keys the server runs in open mode. |
| **Success** | `{"data": ..., "meta": {"request_id": "...", ...}}` |
| **Error** | `{"error": {"code": "...", "message": "...", "hint": "what to do next", "request_id": "..."}}` |
| **Error codes** | `bad_request` 400, `unauthorized` 401, `not_found` 404, `validation_error` 422, `rate_limited` 429, `upstream_error` 502, `unavailable` 503, `internal_error` 500 |
| **Per-product keys** | Each product can have its own key with its own permissions and limits: see [BRIDGE.md](BRIDGE.md). A call the key may not make answers `403 forbidden`. |
| **Rate limits** | `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` on every response. `429` carries `Retry-After` (seconds). |
| **Symbols** | `EXCHANGE:TICKER`, upper case, for example `NSE:RELIANCE`, `NASDAQ:AAPL`, `BINANCE:BTCUSDT`. Use `/v1/symbols/search` to find them. |
| **Currency** | Monetary values are in the listing currency (rupees for NSE/BSE) and every response says which (`currency`). Exchanges outside NSE, BSE, NASDAQ, NYSE, AMEX, LSE, TSX, ASX and XETR fall back to USD, labelled as such. |
| **Freshness** | Every quote has `realtime`, `delayed` and `delay_seconds`. NSE and NASDAQ are normally delayed 15 minutes (`delay_seconds: 900`); crypto is real time. |
| **Request IDs** | Send `X-Request-ID` to correlate logs; otherwise one is generated and returned. |

## Endpoints

| Method | Path | What it returns |
|---|---|---|
| GET | `/v1/health` | Liveness (no key needed) |
| GET | `/v1/status` | Version, live-hub state, limits |
| GET | `/v1/markets` | Valid markets, categories, timeframes and screener fields in plain language (no key) |
| GET | `/v1/glossary` | What every market term means (no key) |
| GET | `/v1/symbols/resolve?q=reliance` | Best `EXCHANGE:TICKER` for a name, plus alternatives. `country=IN` breaks ties (default from `DEFAULT_COUNTRY`) |
| GET | `/v1/operations` | Every operation, including plugins (no key) |
| GET | `/v1/schema` | JSON Schema for each operation's parameters (no key) |
| GET | `/v1/asyncapi.json` | AsyncAPI description of the WebSocket for client generators (no key) |
| GET | `/v1/symbols/search?q=reliance` | Matching symbols |
| GET | `/v1/quotes?symbols=NSE:RELIANCE,NASDAQ:AAPL` | Latest quote for up to 100 symbols. `meta.not_found` lists unknown ones |
| GET | `/v1/symbols/{exchange}/{ticker}` | Profile and key statistics |
| GET | `/v1/symbols/{exchange}/{ticker}/fundamentals` | Fundamentals |
| GET | `/v1/symbols/{exchange}/{ticker}/technicals?timeframe=1d` | Technical indicator values |
| GET | `/v1/symbols/{exchange}/{ticker}/candles?timeframe=1d&limit=100` | OHLCV, oldest first, `time` in epoch seconds (UTC) |
| GET | `/v1/symbols/{exchange}/{ticker}/news?limit=20` | Latest headlines |
| GET | `/v1/markets/movers?market=stocks-india&category=gainers&limit=25` | Gainers, losers, most active. Liquid main-exchange listings only |
| POST | `/v1/screener` | Your own conditions, see below |
| GET | `/v1/calendar/earnings?markets=india&from=2026-10-01&to=2026-10-14` | Earnings events |
| GET | `/v1/symbols/{exchange}/{ticker}/corporate-actions` | Dividends, splits, bonus (needs the `nse` source) |
| GET | `/v1/markets/breadth` | Advances / declines for the day (needs the `nse` source) |
| GET | `/v1/calendar/dividends?markets=india` | Dividend events |
| GET (SSE) | `/v1/stream/quotes?symbols=...` | Continuous quotes |
| WebSocket | `/v1/ws` | **Main interface.** Ask for anything and receive live quotes and lists on one connection |

Timeframes: `1m 5m 15m 30m 1h 2h 4h 1d 1w 1M`. Movers markets: `stocks-india stocks-usa stocks-uk stocks-canada stocks-australia crypto forex`; categories `gainers losers most-active penny-stocks`, plus `pre-market-*` and `after-hours-*` for `stocks-usa` only.

### Quote object

```json
{
  "symbol": "NSE:RELIANCE", "status": "ok",
  "price": 1167.7, "change": -19.3, "change_percent": -1.63,
  "open": 1180.1, "high": 1183.9, "low": 1160.8, "volume": 16771221,
  "bid": null, "ask": null, "currency": "INR", "name": "RELIANCE",
  "description": "Reliance Industries Limited", "exchange": "NSE", "type": "stock",
  "update_mode": "delayed_streaming_900", "freshness": "15 min delayed", "realtime": false, "delayed": true, "delay_seconds": 900
}
```

A symbol that does not exist comes back as `{"symbol": "...", "status": "error", "error": "symbol_not_found"}` on streams and in `meta.not_found` on `/v1/quotes`.

### Screener

```bash
curl -X POST https://api.example.com/v1/screener \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{
    "market": "india",
    "conditions": [
      {"field": "market_cap_basic", "op": "gte", "value": 500000000000},
      {"field": "change", "op": "between", "value": [1, 5]}
    ],
    "sort_by": "market_cap_basic", "sort_order": "desc", "limit": 25
  }'
```

Operators: `gt gte lt lte eq neq in between`. `main_only` (default `true`) keeps results to the market's main exchange (NSE for India). Market cap values are in the listing currency.

## Live quotes

### WebSocket `/v1/ws`

```text
→ {"action": "subscribe", "symbols": ["BINANCE:BTCUSDT", "NSE:TCS"]}
← {"type": "subscribed", "symbols": ["BINANCE:BTCUSDT", "NSE:TCS"], "rejected": []}
← {"type": "quote", "data": { ...quote object... }}
← {"type": "heartbeat", "time": 1790850543}          (every 20 s of silence)
→ {"action": "unsubscribe", "symbols": ["NSE:TCS"]}
→ {"action": "ping"}                                  ← {"type": "pong"}
```

Errors arrive as `{"type": "error", "code": "...", "message": "..."}` and fatal ones close the socket with code `4401` (bad key) or `4429` (limit). Slow clients receive only the latest quote per symbol, never a backlog.

### Server-sent events `/v1/stream/quotes?symbols=A,B`

Events named `quote` with the quote object as JSON, and `: keep-alive` comment lines.

## Limits (defaults, all configurable)

120 requests per minute per key, 100 symbols per live connection, 5 live connections per key, 500 distinct symbols tracked upstream across all clients. One shared upstream connection serves every client, so ten clients watching the same symbol cost one upstream subscription.

## SDKs

* Python: `clients/python` (`pip install ./clients/python[live,pandas]`)
* JavaScript and TypeScript: `clients/js` (zero dependencies)

See [INTEGRATION.md](INTEGRATION.md).
