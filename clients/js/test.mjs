// Offline tests for the JS SDK. Run: node --test clients/js/test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { TickvaleClient, MarketClient, AuthError, RateLimitError, NotFoundError, ConnectionFailed } from './index.js';

const reply = (status, body, headers = {}) => ({
  ok: status < 400, status, headers: new Headers(headers), json: async () => body,
});
const ok = (data, meta = {}) => reply(200, { data, meta: { request_id: 'r1', ...meta } });
const err = (status, code, message, headers) => reply(status, { error: { code, message, request_id: 'r9' } }, headers);

function client(responses, opts = {}) {
  const calls = [];
  const fetch = async (url, init) => {
    calls.push({ url: String(url), init });
    const next = responses.shift();
    if (next instanceof Error) throw next;
    return next;
  };
  return { c: new TickvaleClient({ baseUrl: 'https://api.test/', apiKey: 'k', maxRetries: 2, fetch, ...opts }), calls };
}
const fast = () => { const t = globalThis.setTimeout; globalThis.setTimeout = (f) => t(f, 0); return () => { globalThis.setTimeout = t; }; };

test('alias keeps the old name working', () => assert.equal(MarketClient, TickvaleClient));

test('sends the key and unwraps data', async () => {
  const { c, calls } = client([ok([{ symbol: 'NSE:TCS' }])]);
  assert.deepEqual(await c.quotes(['NSE:TCS', 'NASDAQ:AAPL']), [{ symbol: 'NSE:TCS' }]);
  const u = new URL(calls[0].url);
  assert.equal(u.pathname, '/v1/quotes');
  assert.equal(u.searchParams.get('symbols'), 'NSE:TCS,NASDAQ:AAPL');
  assert.equal(calls[0].init.headers['X-API-Key'], 'k');
});

test('plain names are resolved once and cached', async () => {
  const { c, calls } = client([
    ok({ best: { full_symbol: 'NSE:RELIANCE' }, alternatives: [] }), ok([{ symbol: 'NSE:RELIANCE' }]), ok([{ symbol: 'NSE:RELIANCE' }]),
  ]);
  await c.quote('Reliance');
  await c.quote('reliance');
  assert.deepEqual(calls.map(x => new URL(x.url).pathname), ['/v1/symbols/resolve', '/v1/quotes', '/v1/quotes']);
});

test('autoResolve can be switched off', async () => {
  const { c } = client([], { autoResolve: false });
  await assert.rejects(() => c.symbol('reliance'), TypeError);
});

test('typed errors and no retry on 401/404', async () => {
  const a = client([err(401, 'unauthorized', 'bad key')]);
  await assert.rejects(() => a.c.quotes('NSE:TCS'), e => e instanceof AuthError && e.requestId === 'r9' && e.status === 401);
  assert.equal(a.calls.length, 1);
  const n = client([err(404, 'not_found', 'nope')]);
  await assert.rejects(() => n.c.symbol('NSE:X'), NotFoundError);
});

test('retries 429 using Retry-After then succeeds', async () => {
  const restore = fast();
  try {
    const { c, calls } = client([err(429, 'rate_limited', 'slow', { 'retry-after': '0' }), ok([1])]);
    assert.deepEqual(await c.movers(), [1]);
    assert.equal(calls.length, 2);
  } finally { restore(); }
});

test('gives up after maxRetries', async () => {
  const restore = fast();
  try {
    const { c, calls } = client([1, 2, 3].map(() => err(429, 'rate_limited', 'slow', { 'retry-after': '0' })));
    await assert.rejects(() => c.movers(), RateLimitError);
    assert.equal(calls.length, 3);
  } finally { restore(); }
});

test('network failures become ConnectionFailed', async () => {
  const restore = fast();
  try {
    const { c } = client([new Error('down'), new Error('down'), new Error('down')]);
    await assert.rejects(() => c.health(), ConnectionFailed);
  } finally { restore(); }
});

test('screener posts a JSON body', async () => {
  const { c, calls } = client([ok([])]);
  await c.screener({ market: 'india', conditions: [{ field: 'close', op: 'gt', value: 1 }], limit: 5 });
  const body = JSON.parse(calls[0].init.body);
  assert.equal(calls[0].init.method, 'POST');
  assert.deepEqual([body.market, body.limit, body.conditions[0].op], ['india', 5, 'gt']);
});

// ── WebSocket client ────────────────────────────────────────────────
import { TickvaleSocket, AuthError as SockAuthError } from './index.js';

/** A scripted stand-in for WebSocket. `script(ws, frame)` plays the server. */
function fakeSocketClass(script, log = []) {
  return class FakeWS {
    constructor(url) {
      this.url = url; this.readyState = 0; this.sent = []; log.push(this);
      queueMicrotask(() => { this.readyState = 1; script.onOpen?.(this, log.length); });
    }
    send(text) { const f = JSON.parse(text); this.sent.push(f); script.onFrame?.(this, f, log.length); }
    close() { this.readyState = 3; queueMicrotask(() => this.onclose?.()); }
    push(frame) { queueMicrotask(() => this.onmessage?.({ data: JSON.stringify(frame) })); }
    drop() { this.readyState = 3; queueMicrotask(() => this.onclose?.()); }
  };
}
const HELLO = { type: 'hello', service: 'tickvale', version: '3', operations: [], channels: [], limits: {}, note: '' };
const tick = (ms = 5) => new Promise(r => setTimeout(r, ms));

test('socket: call matches answers to requests by id and unwraps data', async () => {
  const log = [];
  const WebSocket = fakeSocketClass({
    onOpen: ws => ws.push(HELLO),
    onFrame: (ws, f) => { if (f.op === 'quotes') ws.push({ type: 'result', id: f.id, op: 'quotes', data: [{ symbol: 'NSE:TCS' }], meta: { count: 1 } }); },
  }, log);
  const sock = new TickvaleClient({ baseUrl: 'https://api.test', apiKey: 'k with space', WebSocket }).socket();
  const hello = await sock.connect();
  assert.equal(hello.service, 'tickvale');
  assert.equal(log[0].url, 'wss://api.test/v1/ws?api_key=k%20with%20space');
  assert.deepEqual(await sock.call('quotes', { symbols: ['reliance'] }), [{ symbol: 'NSE:TCS' }]);
  assert.deepEqual((await sock.callFull('quotes', {})).meta, { count: 1 });
  sock.close();
});

test('socket: error frames become typed errors and the socket stays usable', async () => {
  const WebSocket = fakeSocketClass({
    onOpen: ws => ws.push(HELLO),
    onFrame: (ws, f) => ws.push(f.op === 'bad'
      ? { type: 'error', id: f.id, code: 'not_found', message: 'Nothing matched', hint: 'Try another spelling' }
      : { type: 'result', id: f.id, op: f.op, data: 'fine', meta: {} }),
  });
  const sock = new TickvaleClient({ baseUrl: 'https://api.test', WebSocket }).socket();
  await assert.rejects(() => sock.call('bad'), e => e instanceof NotFoundError && /Try another spelling/.test(e.message));
  assert.equal(await sock.call('good'), 'fine');
  sock.close();
});

test('socket: rejected at connect with a bad key is fatal, not retried', async () => {
  const log = [];
  const WebSocket = fakeSocketClass({ onOpen: ws => { ws.push({ type: 'error', id: null, code: 'unauthorized', message: 'Invalid API key.' }); queueMicrotask(() => ws.drop()); } }, log);
  const sock = new TickvaleClient({ baseUrl: 'https://api.test', apiKey: 'bad', WebSocket }).socket();
  await assert.rejects(() => sock.connect(), SockAuthError);
  await tick(30);
  assert.equal(log.length, 1);
  await assert.rejects(() => sock.call('markets'), SockAuthError);
});

test('socket: reconnects and subscribes again with the resolved symbols', async () => {
  const log = [];
  const WebSocket = fakeSocketClass({
    onOpen: ws => ws.push(HELLO),
    onFrame: (ws, f, n) => {
      if (f.op === 'subscribe' && f.params.channel === 'quotes') {
        ws.push({ type: 'subscribed', id: f.id, channel: 'quotes', symbols: ['BINANCE:BTCUSDT'], rejected: [], resolved: { bitcoin: 'BINANCE:BTCUSDT' } });
        if (n === 2) ws.push({ type: 'quote', data: { symbol: 'BINANCE:BTCUSDT', price: 1 } });
      }
    },
  }, log);
  const sock = new TickvaleClient({ baseUrl: 'https://api.test', WebSocket }).socket({ maxBackoffMs: 10 });
  const events = [];
  ['disconnected', 'connected'].forEach(e => sock.on(e, () => events.push(e)));
  const quotes = [];
  sock.on('quote', q => quotes.push(q.price));
  await sock.connect();
  const reply = await sock.subscribe('quotes', { symbols: ['bitcoin'] });
  assert.deepEqual(reply.resolved, { bitcoin: 'BINANCE:BTCUSDT' });
  log[0].drop();
  for (let i = 0; i < 400 && !quotes.length; i++) await tick(10);   // backoff is >= 1 s
  assert.deepEqual(events, ['disconnected', 'connected']);
  assert.deepEqual(log[1].sent.map(f => f.params), [{ channel: 'quotes', symbols: ['BINANCE:BTCUSDT'] }]);
  assert.deepEqual(quotes, [1]);
  sock.close();
});

test('socket: quotes() yields until the socket closes', async () => {
  const WebSocket = fakeSocketClass({ onOpen: ws => { ws.push(HELLO); setTimeout(() => { ws.push({ type: 'quote', data: { price: 1 } }); ws.push({ type: 'quote', data: { price: 2 } }); }, 5); } });
  const sock = new TickvaleClient({ baseUrl: 'https://api.test', WebSocket }).socket();
  await sock.connect();
  const got = [];
  setTimeout(() => sock.close(), 60);
  for await (const q of sock.quotes()) got.push(q.price);
  assert.deepEqual(got, [1, 2]);
});

test('socket: a closed socket refuses new requests', async () => {
  const WebSocket = fakeSocketClass({ onOpen: ws => ws.push(HELLO) });
  const sock = new TickvaleClient({ baseUrl: 'https://api.test', WebSocket }).socket();
  await sock.connect();
  sock.close();
  await assert.rejects(() => sock.call('markets'), ConnectionFailed);
});
