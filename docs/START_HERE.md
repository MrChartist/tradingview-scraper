# Start here (you know code, not markets)

Everything you need to use Tickvale without learning finance first. Plain words, then working code.

## Five ideas, in one minute

1. **A symbol is a code for one tradable thing**, written `EXCHANGE:TICKER`. `NSE:RELIANCE` is Reliance Industries on India's NSE exchange. You rarely need to know codes: ask for `"reliance"` and Tickvale finds it.
2. **Free prices are usually 15 minutes late.** Every price has `freshness` (`"15 min delayed"`, `"real time"`) and the booleans `realtime` and `delayed`. Show that to your users. Crypto is real time; stocks usually are not.
3. **Money is in the stock's own currency.** Indian companies are in rupees (`currency: "INR"`). Nothing is converted. Indians write big numbers in lakh (1,00,000) and crore (1,00,00,000); 1 lakh crore is a trillion.
4. **A candle is one bar on a price chart**: the open, high, low and close for a stretch of time (a minute, a day). `candles(...)` returns them oldest first.
5. **Markets close.** Outside trading hours the price just stays at the last close. That is not a bug.

Every term has a plain-language explanation: call `GET /v1/glossary` (no key needed), or `client.glossary()`.

## Try it in two minutes

Run the server (no key needed locally):

```bash
pip install -r requirements.txt -r api/requirements.txt
uvicorn api.main:app --port 8000
# open http://localhost:8000 for the web app, http://localhost:8000/docs for the API
```

Python:

```python
import sys; sys.path.insert(0, "clients/python")
from tickvale import TickvaleClient

client = TickvaleClient("http://localhost:8000")
q = client.quote("reliance")                       # plain names work
print(q["price"], q["currency"], q["freshness"])   # 1167.7 INR 15 min delayed

print(client.quotes(["apple", "bitcoin"]))         # several at once
print(client.movers("stocks-india", "gainers", 5)) # today's biggest risers
df = client.candles("tcs", "1d", 100, as_dataframe=True)   # for charts and analysis
```

JavaScript:

```js
import { TickvaleClient } from './clients/js/index.js';
const client = new TickvaleClient({ baseUrl: 'http://localhost:8000' });
console.log(await client.quote('reliance'));
```

Live prices:

```python
import asyncio
async def main():
    async for q in client.stream(["bitcoin", "NSE:TCS"]):
        print(q["symbol"], q["price"], q["freshness"])
asyncio.run(main())
```

## Which call do I need?

| I want to... | Call |
|---|---|
| Find the code for a company | `client.resolve("tata motors")` or `GET /v1/symbols/resolve?q=` |
| Show a price | `client.quote("reliance")` |
| Draw a chart | `client.candles("reliance", "1d", 200)` |
| List today's winners and losers | `client.movers("stocks-india", "gainers")` |
| Find stocks that match rules | `client.screener("india", [{"field": "change", "op": "gt", "value": 3}])` |
| Show company size, profit and so on | `client.symbol("reliance")` and `client.fundamentals("reliance")` |
| Show news | `client.news("reliance")` |
| Know when companies report profits | `client.earnings("india")` |
| See valid markets, timeframes and filter fields | `client.markets()` |
| Stream prices | `client.stream([...])` |

## When something goes wrong

Every error has a `message` and a `hint` that says what to do.

| You see | It means | Do this |
|---|---|---|
| `401 unauthorized` | Missing or wrong API key | Send `X-API-Key`. Locally, leave `API_KEYS` unset and no key is needed. |
| `400 Invalid symbol format` | You passed a name where a code is expected (raw HTTP only) | Use `/v1/symbols/resolve`, or use the SDK, which does it for you |
| `404 not_found` | That symbol does not exist or has no data | Search by name; coins and indexes have no company fundamentals |
| `429 rate_limited` | Too many requests this minute | Wait `Retry-After` seconds; the SDKs do this automatically |
| `502 upstream_error` | The data source hiccuped | Retry; the SDKs do |
| A price looks old | Delayed data or a closed market | Check `freshness` |
| `null` values | The data does not exist (for example a loss-making company has no P/E) | Handle `null` |

## Before you put this in front of customers

Read [INTEGRATION.md](INTEGRATION.md#5-read-this-before-going-to-production). The data comes from public web endpoints with their own terms, so get advice before a commercial launch.
