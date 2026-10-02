# open-market-client (Python)

```bash
pip install ./clients/python            # REST only
pip install "./clients/python[live,pandas]"
```

```python
from open_market_client import MarketClient, RateLimitError

client = MarketClient("https://api.example.com", api_key="...")
print(client.quote("NSE:RELIANCE")["price"])
df = client.candles("NSE:RELIANCE", "1d", 200, as_dataframe=True)

import asyncio
async def main():
    async for q in client.stream(["BINANCE:BTCUSDT", "NSE:TCS"]):
        print(q["symbol"], q["price"], "delayed" if q["delayed"] else "realtime")
asyncio.run(main())
```

Retries 429/502/503/504 and network errors with backoff; raises `AuthError`, `RateLimitError`, `NotFoundError`, `UpstreamError`, `ConnectionFailed`. Full reference: `docs/API.md`.
