// Tickvale from Node 22+ (or any modern browser build step). Run from the repo:
//   TICKVALE_URL=http://localhost:8000 TICKVALE_KEY=your-key node examples/node_quickstart.mjs
import { TickvaleClient } from '../clients/js/index.js';

const client = new TickvaleClient({ baseUrl: process.env.TICKVALE_URL || 'http://localhost:8000', apiKey: process.env.TICKVALE_KEY });

// 1. Plain names work; every price says how fresh it is.
const q = await client.quote('reliance');
console.log(`${q.symbol}: ${q.price} ${q.currency} (${q.freshness})`);

// 2. Today's biggest gainers
for (const row of await client.movers({ market: 'stocks-india', category: 'gainers', limit: 3 })) {
  console.log(`${row.symbol}: ${row.change.toFixed(2)}%`);
}

// 3. One live connection: ask, then listen
const sock = client.socket();
await sock.connect();
await sock.subscribe('quotes', { symbols: ['bitcoin', 'ethereum'] });
let seen = 0;
for await (const quote of sock.quotes()) {
  console.log('live:', quote.symbol, quote.price, quote.freshness);
  if (++seen >= 4) break;
}
sock.close();
