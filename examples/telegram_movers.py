"""Post today's movers to a Telegram channel or chat.

    TICKVALE_URL=https://your-host TICKVALE_KEY=key \\
    TELEGRAM_BOT_TOKEN=123:abc TELEGRAM_CHAT_ID=@yourchannel \\
    python examples/telegram_movers.py --market stocks-india --category gainers --limit 5

    python examples/telegram_movers.py --dry-run          # print the message, send nothing

Run it from cron or any scheduler. Set MESSAGE_FOOTER to add your own sign-off line.
The message is short paragraphs with a clear heading and no emoji. It says when prices are delayed.

NOTE: the message building is tested; sending uses Telegram's public Bot API and has not been run from tests.
"""
import argparse
import os
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "clients", "python"))   # not needed once installed

from tickvale import TickvaleClient  # noqa: E402

TITLES = {"gainers": "Top gainers", "losers": "Top losers", "most-active": "Most traded", "penny-stocks": "Low-priced stocks"}
MARKETS = {"stocks-india": "India (NSE)", "stocks-usa": "US", "stocks-uk": "UK", "crypto": "Crypto"}


def crore_lakh(n: float) -> str:
    return f"{n / 1e7:.2f} Cr" if n >= 1e7 else f"{n / 1e5:.2f} L" if n >= 1e5 else f"{n:,.0f}"


def build_message(rows: list, market: str, category: str, footer: str = "") -> str:
    """Plain text. Rupee amounts use lakh/crore; everything else uses K/M/B."""
    indian = market == "stocks-india"
    lines = [f"{TITLES.get(category, category.title())}: {MARKETS.get(market, market)}"]
    if market != "crypto":
        lines.append("Prices are about 15 minutes delayed.")
    lines.append("")
    for i, r in enumerate(rows, 1):
        name = r.get("description") or r.get("name") or r["symbol"]
        price = r.get("close")
        symbol = "₹" if indian else ""
        volume = r.get("volume") or 0
        vol = crore_lakh(volume) if indian else (f"{volume / 1e6:.2f}M" if volume >= 1e6 else f"{volume:,.0f}")
        change = r.get("change")
        lines.append(f"{i}. {name} ({r['symbol']})")
        lines.append(f"   {symbol}{price:,.2f}   {change:+.2f}%   Volume {vol}" if price is not None and change is not None else "   no price")
        lines.append("")
    if footer:
        lines.append(footer)
    return "\n".join(lines).rstrip() + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--market", default="stocks-india")
    ap.add_argument("--category", default="gainers")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true", help="Print the message instead of sending it")
    args = ap.parse_args(argv)

    client = TickvaleClient(os.getenv("TICKVALE_URL", "http://localhost:8000"), api_key=os.getenv("TICKVALE_KEY"))
    text = build_message(client.movers(args.market, args.category, args.limit), args.market, args.category, os.getenv("MESSAGE_FOOTER", ""))
    if args.dry_run:
        print(text)
        return 0
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (or use --dry-run).", file=sys.stderr)
        return 2
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat, "text": text}, timeout=15)
    r.raise_for_status()
    print("Sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
