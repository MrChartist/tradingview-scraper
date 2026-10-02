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
