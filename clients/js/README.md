# tickvale (JavaScript / TypeScript)

Zero dependencies. Node 18+ (Node 22+ for live streaming, or pass `{ WebSocket }` from `ws`) and modern browsers.

```js
import { TickvaleClient, RateLimitError } from './index.js';

const client = new TickvaleClient({ baseUrl: 'https://api.example.com', apiKey: process.env.MARKET_KEY });
console.log(await client.quote('NSE:RELIANCE'));
console.log(await client.candles('NSE:RELIANCE', { timeframe: '1d', limit: 200 }));

const live = client.stream(['BINANCE:BTCUSDT', 'NSE:TCS'], {
  onQuote: q => console.log(q.symbol, q.price, q.delayed ? 'delayed' : 'realtime'),
  onError: e => console.error(e.name, e.message),
});
// live.subscribe('NASDAQ:AAPL'); live.close();
```

## One connection for everything

```js
const sock = client.socket();
await sock.connect();
console.log(await sock.call('quotes', { symbols: ['reliance', 'bitcoin'] }));   // ask (plain names work)
sock.on('quote', q => console.log(q.symbol, q.price, q.freshness));
await sock.subscribe('quotes', { symbols: ['bitcoin', 'NSE:TCS'] });
await sock.subscribe('movers', { market: 'stocks-india', category: 'gainers' });
sock.on('update', u => console.log(u.key, u.data.length));
```

It reconnects by itself and subscribes again. See `docs/WEBSOCKET.md`.

Types are in `index.d.ts`. Browsers cannot set WebSocket headers, so the key is sent as `?api_key=` for streams: keep keys server-side for anything public. Full reference: `docs/API.md`.
