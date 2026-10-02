"""Tickvale from Python. Run from the repo:  python examples/python_quickstart.py

    TICKVALE_URL=http://localhost:8000 TICKVALE_KEY=your-key python examples/python_quickstart.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "clients", "python"))   # not needed once installed

from tickvale import TickvaleClient  # noqa: E402

client = TickvaleClient(os.getenv("TICKVALE_URL", "http://localhost:8000"), api_key=os.getenv("TICKVALE_KEY"))

# 1. Plain names work. Money is in the stock's own currency; "freshness" says how old the price is.
q = client.quote("reliance")
print(f"{q['symbol']}: {q['price']} {q['currency']} ({q['freshness']})")

# 2. Several at once
for item in client.quotes(["apple", "bitcoin"]):
    print(f"{item['symbol']}: {item['price']} ({item['freshness']})")

# 3. A price history ready for charts or maths (needs pandas)
try:
    print(client.candles("tcs", "1d", 5, as_dataframe=True).round(2))
except ImportError:
    print("(install pandas to get candles as a table)")

# 4. Today's biggest gainers
for row in client.movers("stocks-india", "gainers", 3):
    print(f"{row['symbol']}: {row['change']:+.2f}%")


# 5. One live connection: ask, then listen
async def live():
    async with client.socket() as sock:
        await sock.subscribe("quotes", symbols=["bitcoin", "ethereum"])
        count = 0
        async for quote in sock.quotes():
            print("live:", quote["symbol"], quote["price"], quote["freshness"])
            count += 1
            if count >= 4:
                break

asyncio.run(asyncio.wait_for(live(), 30))
