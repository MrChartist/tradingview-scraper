# Connecting your products to Tickvale

Tickvale is the one place your products and projects get market data from. Each product connects with **its own key**, sees only what it needs, and none of them has to know where the data really comes from.

```text
   Nesto (app)      reports (jobs)     Google Sheet     Telegram channel     a future project
        |                 |                 |                  |                    |
        +------ own key ---+------ own key --+------ own key ---+------- own key -----+
                                          |
                                   Tickvale (this server)
                       one WebSocket + REST · limits · plain-name search
                                          |
                          data sources, in the order you choose
                      (today: TradingView · later: brokers, exchanges)
```

## 1. Give each product its own key

One command per product. The key is shown once; only its hash is stored.

```bash
python -m api.keys nesto    --operations quotes,candles,symbol,movers,resolve --channels quotes --rate 300 --max-symbols 50 --append clients.json
python -m api.keys reports  --operations fundamentals,candles,earnings,dividends --rate 60 --append clients.json
python -m api.keys sheet    --operations quotes --rate 60 --append clients.json
```

Put `CLIENTS_FILE=clients.json` in your environment and restart. Now:

* **Least privilege.** A key that may only read `quotes` cannot run a screener, even if it leaks. Breaking one product's key does not touch the others.
* **Separate limits.** A noisy job cannot use up the app's allowance (`rate_limit_per_minute` per client).
* **Revoking** a product is deleting its entry and restarting.
* **A forbidden call** answers `403 forbidden` with a hint naming what the key may do. `hello` on the WebSocket only lists what that key can use.
* Public help (`markets`, `glossary`, `schema`, `ping`) is always open.

See [`clients.example.json`](../clients.example.json). Keep the real file out of git (it is listed in `.gitignore` if you name it `clients.json`).

## 2. Pick how each product connects

| Your product is... | Use | Recipe |
|---|---|---|
| A Python service or notebook | The Python SDK | [`examples/python_quickstart.py`](../examples/python_quickstart.py) |
| A Node or browser app | The JavaScript SDK (zero dependencies) | [`examples/node_quickstart.mjs`](../examples/node_quickstart.mjs) |
| Anything else (Go, Java, PHP, Rust...) | The WebSocket or REST directly, with a client generated from the contract (section 3) | [`examples/curl.sh`](../examples/curl.sh), [WEBSOCKET.md](WEBSOCKET.md) |
| A Google Sheet | One Apps Script function: `=TICKVALE("reliance")` | [`examples/google_sheets.gs`](../examples/google_sheets.gs) |
| A Telegram channel | A small script on a schedule | [`examples/telegram_movers.py`](../examples/telegram_movers.py) |
| A no-code tool (n8n, Zapier, Make) | Its HTTP Request step | Section 4 |

Rules of thumb:

* **One WebSocket per product, not one per user.** The server shares a single upstream feed, so many symbols on one connection is cheap.
* **Keep keys server-side.** A browser app should talk to your backend, which talks to Tickvale. If a page must connect directly, give it its own low-limit key with `quotes` only.
* **Show freshness.** Every price has `freshness`; show "15 min delayed" to your users.
* **Cache on your side** for pages many people see; the server already caches for a minute.

## 3. The contract, for any language

You never need to read our code to build a client.

| What | Where |
|---|---|
| REST: every endpoint, parameter and response | `/openapi.json` (interactive at `/docs`) |
| WebSocket: every operation, parameter and frame | `/v1/asyncapi.json` (AsyncAPI 2.6, works with the AsyncAPI generators) |
| Plain JSON Schema for each operation's parameters | `/v1/schema`, or `{"op": "schema"}` on the socket |
| What this particular key may use | the `hello` message after connecting |

None of these need a key.

## 4. No-code tools (n8n, Zapier, Make)

Use the tool's **HTTP Request** step:

* Method `GET`, URL `https://your-host/v1/quotes?symbols=NSE:RELIANCE,NASDAQ:AAPL`
* Header `X-API-Key` = that tool's own key
* Read `data[0].price`, `data[0].freshness`

For a screener, set the method to `POST`, the body to JSON, and header `Content-Type: application/json`. Schedule it with the tool's own timer. Prefer one call for many symbols over many calls.

## 5. Adding something of your own

Your products can also publish their own operations and live feeds through the same connection (a watchlist, alerts, a portfolio view). See [EXTENDING.md](EXTENDING.md). Where the data comes from is a separate choice: see [PROVIDERS.md](PROVIDERS.md).

## 6. Checklist before a product goes live

1. Its own key, with only the operations and channels it needs, and a sensible `rate_limit_per_minute`.
2. HTTPS in front of the server; `REQUIRE_API_KEY=true`.
3. The product shows `freshness` and never presents delayed data as live.
4. It reconnects (the SDKs do) and handles `null` values.
5. You have read the terms-of-use note in [INTEGRATION.md](INTEGRATION.md#5-read-this-before-going-to-production).
