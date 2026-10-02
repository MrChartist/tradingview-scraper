# Data sources (providers)

Tickvale answers questions by asking one or more **providers**. Today there is one, TradingView. Brokers and exchanges are meant to join it later without changing a single client.

## How it works

```text
request  ->  Tickvale  ->  first provider that can answer  ->  answer (meta.source says who)
                      \-> cannot, or fails?  next provider
```

* Order is set by `PROVIDERS`, for example `PROVIDERS=zerodha,tradingview`: ask the broker first, use TradingView only for what it cannot answer.
* Leave a provider out to switch it off. `PROVIDERS=tradingview` (the default) is the launch setup.
* Every answer carries `meta.source` (and `meta.sources` for a mix of providers in `quotes`), and `/v1/status` and the socket's `hello` list the enabled sources with their capabilities.
* If a provider has a bad moment (session expired, network), the request falls through to the next one instead of failing.
* If nobody enabled can answer, you get `501 not_available` with a hint listing the enabled sources.

## Capabilities

A provider lists what it can do; everything else is skipped.

| Capability | Answers | Provider method |
|---|---|---|
| `search` | Find symbols by name | `search(q)` |
| `quotes` | Latest prices | `quotes(symbols) -> (quotes, not_found)` |
| `candles` | Price history | `candles(exchange, ticker, timeframe, limit)` |
| `overview` | Profile and key statistics | `overview(exchange, ticker)` |
| `fundamentals` | Company numbers | `fundamentals(exchange, ticker)` |
| `technicals` | Indicator readings | `technicals(exchange, ticker, timeframe)` |
| `news` | Headlines | `news(exchange, ticker, limit, language)` |
| `movers` | Gainers, losers, most active | `movers(market, category, limit)` |
| `screener` | Rule-based search | `screener(market, filters, columns, sort_by, sort_order, limit)` |
| `calendar` | Earnings and dividends | `calendar(kind, markets, ts_from, ts_to, limit)` |

A provider can also say which exchanges it serves (`exchanges = {"NSE", "BSE"}`), so a broker is only asked about its own markets.

## NSE official source (`nse`)

NSE publishes an official MCP server. Tickvale can use it as a source for Indian equities:

```
PROVIDERS=nse,tradingview
```

* Serves: `NSE:` quotes (about 5 minutes delayed in market hours), daily candles (about 5 years), top gainers/losers, corporate actions and market breadth (advances/declines).
* Anything it does not serve (indices, crypto, other exchanges, intraday candles) falls to TradingView. A slow or failing NSE never breaks a request.
* Cold start can take a few seconds; quotes are cached for 15 s, movers for 60 s.
* Set `NSE_MCP_LIVE_URL` / `NSE_MCP_EOD_URL` only if NSE changes the addresses.

**Usage terms: Needs verification.** NSE's terms are reported (via a secondary source) as "informational and educational purposes, not for real-time trading, commercial deployment, or training AI models". Read NSE's own page (https://www.nseindia.com/nse-mcp) before using this in a product. It is off by default.

## Read-only, on purpose

Tickvale only reads data. The names `place_order`, `modify_order` and `cancel_order` are reserved for later, and a provider that declares any of them is **refused at startup** unless `ALLOW_TRADING=true`. Even then, no operation uses them yet. When orders are added they will arrive as their own operations with their own permission, never as a side effect of a data provider, and will need design for the regulations that apply to automated trading.

## Adding a broker (when the time comes)

A broker is a plugin: a module that registers a provider, loaded with `PLUGINS`.

```python
# my_company/zerodha.py
from api.providers import Provider, provider_factory

class ZerodhaProvider(Provider):
    name = "zerodha"
    description = "Zerodha Kite Connect (official API)."
    capabilities = {"quotes", "candles", "search"}
    exchanges = {"NSE", "BSE"}

    def __init__(self, settings):
        ...                                   # read credentials from the environment, never from code

    def quotes(self, symbols):
        ...                                   # return (list_of_quote_dicts, list_of_not_found)
    def candles(self, exchange, ticker, timeframe, limit):
        ...                                   # oldest first: [{timestamp, open, high, low, close, volume}]

@provider_factory("zerodha")
def make(settings):
    return ZerodhaProvider(settings)
```

```bash
PLUGINS=my_company.zerodha
PROVIDERS=zerodha,tradingview
```

Things every broker adapter has to deal with, which is where the other open-source broker projects are worth studying:

* **Login.** Most Indian broker APIs need a fresh access token each day (OAuth, TOTP or a redirect). The adapter owns refreshing it and must fail with a clear message when it cannot.
* **Symbols.** Brokers identify instruments by their own tokens or names. Keep a mapping from `EXCHANGE:TICKER` to the broker's identifier (usually built from the broker's downloadable instrument list).
* **Quote shape.** Return the same fields as every other provider (see [WEBSOCKET.md](WEBSOCKET.md)), including `currency`, `freshness`, `realtime`, `delayed`.
* **Limits.** Respect the broker's rate limits; the cache helper `api.services.cached` is available.
* **Secrets.** Read keys and tokens from environment variables or a secrets store. Never log them.
* **Licensing.** If you take ideas from another project, check its licence. Copying code from an AGPL project into this one would pull this project under the AGPL too; reading it for ideas and writing your own is the safe route.

## What is not pluggable yet

The **live price feed** (`/v1/ws` quotes channel, and SSE) is still the TradingView feed, in `api/live.py`. A broker's live feed will need the hub to accept a pluggable upstream. That is the next step when the first broker arrives; REST-style questions are already provider-routed.
