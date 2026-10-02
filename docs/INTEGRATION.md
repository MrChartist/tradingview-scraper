# Integrating your product

New to markets? Start with [START_HERE.md](START_HERE.md).

This service is a **bridge**: your product talks to one stable, documented API, and the server worries about where the data comes from. Keep that boundary and you can change the data source later without touching your product.

```text
 your product ──► SDK / HTTP ──►  Tickvale API  ──►  data source (today: TradingView public endpoints)
 (web, mobile,     /v1 contract     auth · limits · cache         swappable: only api/services.py and api/live.py
  bots, jobs)      stable schema    currency · freshness flags    know about the source
```

## 1. Run the bridge

```bash
cp .env.example .env
# set API_KEYS=nesto:$(openssl rand -hex 24)   (one named key per consuming product)
# set REQUIRE_API_KEY=true and ENABLE_WEB_UI=false for an API-only deployment
docker compose up -d --build
curl localhost:8000/v1/health
```

Put it behind HTTPS (Caddy, nginx, a cloud load balancer). Keep one worker: the live hub and rate limiter hold state in memory. For very high traffic, run several instances, each with its own upstream connection, behind a limiter at the proxy.

## 2. Call it from your product

The best way in is one WebSocket (see [WEBSOCKET.md](WEBSOCKET.md)); REST is there for simple jobs. To add your own operations or live feeds to the same connection, see [EXTENDING.md](EXTENDING.md).

Python:

```python
from tickvale import TickvaleClient

client = TickvaleClient("https://api.example.com", api_key=KEY)
client.quotes(["NSE:RELIANCE", "NASDAQ:AAPL"])
client.candles("NSE:RELIANCE", timeframe="1d", limit=200, as_dataframe=True)
client.screener("india", [{"field": "market_cap_basic", "op": "gte", "value": 5e11}], limit=20)

async for quote in client.stream(["BINANCE:BTCUSDT", "NSE:TCS"]):   # reconnects by itself
    handle(quote)
```

JavaScript:

```js
import { TickvaleClient } from 'tickvale';

const client = new TickvaleClient({ baseUrl: 'https://api.example.com', apiKey: process.env.MARKET_KEY });
const quotes = await client.quotes(['NSE:RELIANCE', 'NASDAQ:AAPL']);
const live = client.stream(['BINANCE:BTCUSDT'], { onQuote: q => render(q) });
```

Both SDKs retry transient failures (429, 502, 503, 504, network errors) with backoff and `Retry-After`, and raise typed errors: `AuthError`, `RateLimitError`, `NotFoundError`, `UpstreamError`, `ConnectionFailed`.

### Browsers and mobile apps

Do **not** ship the API key inside a public web or mobile app. Call the bridge from your own backend and expose only what your users need. If you must call it from a browser, use a separate low-limit key and set `CORS_ORIGINS` to your site.

## 3. Things your product should handle

* **Freshness.** Show "15 min delayed" when `delayed` is true. Never present a delayed price as live.
* **Currency.** Use the `currency` field. Indian values arrive in rupees; use your own formatter (the web UI shows Lakh and Crore).
* **Missing data.** Fields can be `null`. Unknown symbols appear in `meta.not_found`.
* **Backpressure.** Live clients get the latest quote per symbol, so process quickly or sample.

## 4. The upgrade path

The `/v1` contract does not mention where data comes from. When you outgrow public endpoints you can:

1. Keep clients unchanged and replace the functions in `api/services.py` (REST) and `api/live.py` (streaming) with a licensed vendor, returning the same shapes.
2. Run both: point `/v1` at the new source and keep the old one for fallback.
3. Version breaking changes as `/v2` and run it alongside `/v1`.

The OpenAPI file at `/openapi.json` is the contract to test against.

## 5. Read this before going to production

* **Terms and licensing.** This reads TradingView's public web endpoints. TradingView's terms restrict automated access and redistribution of its data, and exchanges license market data separately. Using it in a commercial product, or showing data to your customers, may breach those terms. Get legal advice and consider a licensed data provider before you launch. The structure above is built so that switching is cheap.
* **Stability.** These endpoints are unofficial and can change or block you without notice. Expect to maintain it. Watch `/v1/status` and the `upstream_error` rate.
* **Security checklist.** Set `API_KEYS`, serve over HTTPS, disable the web UI if unused, set `CORS_ORIGINS` explicitly, give each product its own key, and rotate keys by changing the environment variable and restarting.
* **No investment advice.** Data may be delayed or wrong.
