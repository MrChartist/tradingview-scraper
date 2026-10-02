#!/usr/bin/env bash
# Tickvale from the command line. Needs only curl (and optionally websocat for the live part).
#   TICKVALE_URL=http://localhost:8000 TICKVALE_KEY=your-key ./examples/curl.sh
set -euo pipefail
URL="${TICKVALE_URL:-http://localhost:8000}"
KEY="${TICKVALE_KEY:-}"
auth=(); [ -n "$KEY" ] && auth=(-H "X-API-Key: $KEY")

echo "== Is it up?";                        curl -s "$URL/v1/health"; echo
echo "== What can I ask for? (no key)";     curl -s "$URL/v1/markets" | head -c 300; echo " ..."
echo "== Find the code for a name";         curl -s "${auth[@]}" "$URL/v1/symbols/resolve?q=reliance" | head -c 300; echo " ..."
echo "== Prices (several at once)";         curl -s "${auth[@]}" "$URL/v1/quotes?symbols=NSE:RELIANCE,BINANCE:BTCUSDT"; echo
echo "== Today's biggest gainers in India"; curl -s "${auth[@]}" "$URL/v1/markets/movers?market=stocks-india&category=gainers&limit=3" | head -c 400; echo " ..."
echo "== Stocks that match your rules"
curl -s "${auth[@]}" -H "Content-Type: application/json" "$URL/v1/screener" \
  -d '{"market":"india","conditions":[{"field":"market_cap_basic","op":"gte","value":1000000000000}],"sort_by":"market_cap_basic","limit":3}' | head -c 400; echo " ..."

if command -v websocat >/dev/null; then
  echo "== Live (Ctrl+C to stop)"
  q=""; [ -n "$KEY" ] && q="?api_key=$KEY"
  printf '%s\n' '{"op":"subscribe","params":{"channel":"quotes","symbols":["bitcoin","NSE:TCS"]}}' \
    | websocat -t "${URL/http/ws}/v1/ws$q"
fi
