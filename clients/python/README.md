# tickvale (Python)

```bash
pip install ./clients/python            # REST only
pip install "./clients/python[live,pandas]"
```

```python
from tickvale import TickvaleClient, RateLimitError

client = TickvaleClient("https://api.example.com", api_key="...")
print(client.quote("NSE:RELIANCE")["price"])
df = client.candles("NSE:RELIANCE", "1d", 200, as_dataframe=True)

import asyncio
async def main():
    async for q in client.stream(["BINANCE:BTCUSDT", "NSE:TCS"]):
        print(q["symbol"], q["price"], "delayed" if q["delayed"] else "realtime")
asyncio.run(main())
```

## One connection for everything

```python
async with client.socket() as sock:
    print(await sock.call("quotes", symbols=["reliance", "bitcoin"]))   # ask (plain names work)
    await sock.subscribe("quotes", symbols=["bitcoin", "NSE:TCS"])     # live prices
    await sock.subscribe("movers", market="stocks-india", category="gainers")
    async for event in sock.events():          # quote, update, connected, disconnected
        print(event["type"], event.get("data"))
```

It reconnects by itself and subscribes again. See `docs/WEBSOCKET.md`.

Retries 429/502/503/504 and network errors with backoff; raises `AuthError`, `RateLimitError`, `NotFoundError`, `UpstreamError`, `ConnectionFailed`. Full reference: `docs/API.md`.
