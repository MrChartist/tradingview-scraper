"""Create a key for a product that will connect to Tickvale.

    python -m api.keys nesto
    python -m api.keys nesto --operations quotes,candles,movers --channels quotes --rate 300 --max-symbols 50
    python -m api.keys nesto --append clients.json

The key is shown once. Only its SHA-256 hash goes into the config file, so a leaked file does not leak access.
"""
import argparse
import json
import os
import secrets
import sys

from api.config import CLIENT_NAME, sha256_hex


def build_entry(name: str, digest: str, operations=None, channels=None, rate=None, max_symbols=None) -> dict:
    entry = {"name": name, "key_sha256": digest}
    if operations:
        entry["operations"] = operations
    if channels:
        entry["channels"] = channels
    if rate:
        entry["rate_limit_per_minute"] = rate
    if max_symbols:
        entry["max_symbols"] = max_symbols
    return entry


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m api.keys", description="Create a key for a product that connects to Tickvale.")
    ap.add_argument("name", help="Product name, for example nesto (letters, numbers, dots, dashes, underscores)")
    ap.add_argument("--operations", help="Comma separated operations it may use. Default: all. See /v1/operations.")
    ap.add_argument("--channels", help="Comma separated live feeds it may subscribe to (quotes, movers). Default: all.")
    ap.add_argument("--rate", type=int, help="Requests per minute for this product. Default: the server's setting.")
    ap.add_argument("--max-symbols", type=int, help="Symbols per request / live connection. Default: the server's setting.")
    ap.add_argument("--append", metavar="FILE", help="Add the entry to this CLIENTS_FILE (created if missing).")
    args = ap.parse_args(argv)

    if not CLIENT_NAME.match(args.name):
        print("The name must be 1-40 letters, numbers, dots, dashes or underscores.", file=sys.stderr)
        return 2
    split = lambda v: [x.strip() for x in v.split(",") if x.strip()] if v else None   # noqa: E731
    key = secrets.token_urlsafe(32)
    entry = build_entry(args.name, sha256_hex(key), split(args.operations), split(args.channels), args.rate, args.max_symbols)

    if args.append:
        entries = []
        if os.path.exists(args.append):
            with open(args.append, encoding="utf-8") as fh:
                entries = json.load(fh)
        if any(e.get("name") == args.name for e in entries):
            print(f"{args.append} already has a client called '{args.name}'.", file=sys.stderr)
            return 2
        entries.append(entry)
        with open(args.append, "w", encoding="utf-8") as fh:
            json.dump(entries, fh, indent=2)
            fh.write("\n")
        where = f"Added to {args.append}."
    else:
        where = "Add this to your CLIENTS_FILE (a JSON list):\n\n" + json.dumps(entry, indent=2)

    print(f"Key for '{args.name}' (shown once, store it in that product's secrets):\n\n    {key}\n\n{where}\n\n"
          "Then restart the server with CLIENTS_FILE pointing at the file. The product sends the key as the\n"
          "X-API-Key header (or ?api_key= on a WebSocket).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
