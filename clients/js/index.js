// Tickvale client. Zero dependencies; Node 18+ (Node 22+ for live streaming) and browsers.

export class MarketApiError extends Error {
  constructor(message, { status = null, code = 'error', requestId = null, retryAfter = null } = {}) {
    super(message);
    this.name = 'MarketApiError';
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.retryAfter = retryAfter;
  }
}
export class AuthError extends MarketApiError { constructor(m, o) { super(m, o); this.name = 'AuthError'; } }
export class RateLimitError extends MarketApiError { constructor(m, o) { super(m, o); this.name = 'RateLimitError'; } }
export class NotFoundError extends MarketApiError { constructor(m, o) { super(m, o); this.name = 'NotFoundError'; } }
export class UpstreamError extends MarketApiError { constructor(m, o) { super(m, o); this.name = 'UpstreamError'; } }
export class ConnectionFailed extends MarketApiError { constructor(m, o) { super(m, o); this.name = 'ConnectionFailed'; } }

const RETRY = new Set([429, 502, 503, 504]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const csv = v => (Array.isArray(v) ? v.join(',') : v);
const backoff = attempt => Math.min(500 * 2 ** attempt, 8000) + Math.random() * 250;

function errorFrom(status, body, headers) {
  const err = (body && body.error) || {};
  const retryAfter = Number(headers.get('retry-after'));
  const opts = {
    status, code: err.code || 'error',
    requestId: err.request_id || headers.get('x-request-id'),
    retryAfter: Number.isFinite(retryAfter) && headers.get('retry-after') !== null ? retryAfter : null,
  };
  const msg = err.message || `HTTP ${status}`;
  if (status === 401 || status === 403) return new AuthError(msg, opts);
  if (status === 404) return new NotFoundError(msg, opts);
  if (status === 429) return new RateLimitError(msg, opts);
  if (status >= 502 && status <= 504) return new UpstreamError(msg, opts);
  return new MarketApiError(msg, opts);
}

export class TickvaleClient {
  /**
   * @param {object} options
   * @param {string} [options.baseUrl]   e.g. https://api.example.com
   * @param {string} [options.apiKey]    keep this server-side; never ship it to browsers you do not control
   * @param {number} [options.timeoutMs]
   * @param {number} [options.maxRetries]
   * @param {typeof fetch} [options.fetch]
   * @param {typeof WebSocket} [options.WebSocket]  pass the `ws` package on Node < 22
   */
  constructor({ baseUrl = 'http://localhost:8000', apiKey, timeoutMs = 30000, maxRetries = 3, autoResolve = true, fetch: f, WebSocket: ws } = {}) {
    this.autoResolve = autoResolve;
    this._resolved = new Map();
    this.baseUrl = baseUrl.replace(/\/+$/, '');
    this.apiKey = apiKey;
    this.timeoutMs = timeoutMs;
    this.maxRetries = maxRetries;
    this._fetch = f || globalThis.fetch?.bind(globalThis);
    this._WebSocket = ws || globalThis.WebSocket;
  }

  async _request(method, path, { params, json } = {}) {
    const url = new URL(this.baseUrl + path);
    for (const [k, v] of Object.entries(params || {})) if (v !== undefined && v !== null) url.searchParams.set(k, v);
    const headers = { Accept: 'application/json' };
    if (this.apiKey) headers['X-API-Key'] = this.apiKey;
    if (json !== undefined) headers['Content-Type'] = 'application/json';

    for (let attempt = 0; ; attempt++) {
      let res;
      try {
        res = await this._fetch(url, {
          method, headers, body: json !== undefined ? JSON.stringify(json) : undefined,
          signal: AbortSignal.timeout(this.timeoutMs),
        });
      } catch (e) {
        if (attempt >= this.maxRetries) throw new ConnectionFailed(`Could not reach ${this.baseUrl}: ${e.message}`);
        await sleep(backoff(attempt));
        continue;
      }
      const body = await res.json().catch(() => null);
      if (res.ok) return body;
      const err = errorFrom(res.status, body, res.headers);
      if (RETRY.has(res.status) && attempt < this.maxRetries) {
        await sleep(err.retryAfter !== null ? err.retryAfter * 1000 : backoff(attempt));
        continue;
      }
      throw err;
    }
  }

  async _get(path, params, withMeta = false) {
    const body = await this._request('GET', path, { params });
    return withMeta ? { data: body.data, meta: body.meta } : body.data;
  }

  /** Turn a name like 'reliance' or 'apple' into the best match: { best, alternatives }. */
  resolve(text) { return this._get('/v1/symbols/resolve', { q: text }); }

  /** 'reliance' -> 'NSE:RELIANCE'. Already-qualified symbols pass through. */
  async fullSymbol(text) {
    if (text.includes(':')) return text.toUpperCase();
    if (!this.autoResolve) throw new TypeError("Use EXCHANGE:TICKER, for example 'NSE:RELIANCE' (or enable autoResolve)");
    const key = text.trim().toLowerCase();
    if (!this._resolved.has(key)) this._resolved.set(key, (await this.resolve(text)).best.full_symbol);
    return this._resolved.get(key);
  }

  /** Valid markets, categories, timeframes and screener fields, in plain language. */
  async markets() { return (await this._request('GET', '/v1/markets')).data; }
  /** What every market term means, in plain language. */
  async glossary() { return (await this._request('GET', '/v1/glossary')).data.terms; }

  async _sym(symbol) {
    const [exchange, ...rest] = (await this.fullSymbol(symbol)).split(':');
    return `/v1/symbols/${encodeURIComponent(exchange)}/${encodeURIComponent(rest.join(':'))}`;
  }

  health() { return this._request('GET', '/v1/health'); }
  status() { return this._get('/v1/status'); }

  /** Latest quotes (max 100). With withMeta, resolves to { data, meta } and meta.not_found lists unknown symbols. */
  async quotes(symbols, { withMeta = false } = {}) {
    const names = Array.isArray(symbols) ? symbols : [symbols];
    const full = await Promise.all(names.map(n => this.fullSymbol(n)));
    return this._get('/v1/quotes', { symbols: full.join(',') }, withMeta);
  }
  async quote(symbol) {
    const { data } = await this.quotes([symbol], { withMeta: true });
    if (!data.length) throw new NotFoundError(`Symbol not found: ${symbol}`, { status: 404, code: 'not_found' });
    return data[0];
  }
  search(q, limit = 10) { return this._get('/v1/symbols/search', { q, limit }); }
  async symbol(symbol) { return this._get(await this._sym(symbol)); }
  async fundamentals(symbol) { return this._get(`${await this._sym(symbol)}/fundamentals`); }
  async technicals(symbol, timeframe = '1d') { return this._get(`${await this._sym(symbol)}/technicals`, { timeframe }); }
  async candles(symbol, { timeframe = '1d', limit = 100 } = {}) { return this._get(`${await this._sym(symbol)}/candles`, { timeframe, limit }); }
  async news(symbol, { limit = 20, language = 'en' } = {}) { return this._get(`${await this._sym(symbol)}/news`, { limit, language }); }
  movers({ market = 'stocks-india', category = 'gainers', limit = 25 } = {}) { return this._get('/v1/markets/movers', { market, category, limit }); }

  /** conditions: [{ field: 'market_cap_basic', op: 'gte', value: 5e11 }] — ops: gt gte lt lte eq neq in between */
  async screener({ market = 'india', conditions = [], columns = null, sortBy = 'volume', sortOrder = 'desc', limit = 25, mainOnly = true, withMeta = false } = {}) {
    const body = await this._request('POST', '/v1/screener', {
      json: { market, conditions, columns, sort_by: sortBy, sort_order: sortOrder, limit, main_only: mainOnly },
    });
    return withMeta ? { data: body.data, meta: body.meta } : body.data;
  }
  earnings({ markets = 'india', from, to, limit = 100 } = {}) { return this._get('/v1/calendar/earnings', { markets: csv(markets), from, to, limit }); }
  dividends({ markets = 'india', from, to, limit = 100 } = {}) { return this._get('/v1/calendar/dividends', { markets: csv(markets), from, to, limit }); }

  /**
   * Live quotes over WebSocket with automatic reconnect and re-subscribe.
   * Browsers cannot set headers on a WebSocket, so the key travels as ?api_key= there.
   * @returns {{ subscribe(symbols), unsubscribe(symbols), close() }}
   */
  stream(symbols, { onQuote, onError, onStatus, reconnect = true, maxBackoffMs = 30000 } = {}) {
    if (!this._WebSocket) throw new Error('No WebSocket available. Use Node 22+, a browser, or pass { WebSocket } from the "ws" package.');
    const wanted = new Set(typeof symbols === 'string' ? [symbols] : symbols || []);
    const wsUrl = this.baseUrl.replace(/^http/, 'ws') + '/v1/ws' + (this.apiKey ? `?api_key=${encodeURIComponent(this.apiKey)}` : '');
    let socket = null, closed = false, delay = 1000, timer = null;

    const send = msg => { if (socket && socket.readyState === 1) socket.send(JSON.stringify(msg)); };
    const open = () => {
      socket = new this._WebSocket(wsUrl);
      socket.onopen = () => {
        delay = 1000;
        onStatus?.('connected');
        if (wanted.size) send({ action: 'subscribe', symbols: [...wanted] });
      };
      socket.onmessage = ev => {
        let msg; try { msg = JSON.parse(ev.data); } catch { return; }
        if (msg.type === 'quote') onQuote?.(msg.data);
        else if (msg.type === 'error') {
          const fatal = msg.code === 'unauthorized' || msg.code === 'rate_limited';
          onError?.(fatal ? (msg.code === 'unauthorized' ? new AuthError(msg.message, { status: 401, code: msg.code }) : new RateLimitError(msg.message, { status: 429, code: msg.code }))
                          : new MarketApiError(msg.message, { code: msg.code }));
          if (fatal) { closed = true; socket.close(); }
        }
      };
      socket.onclose = () => {
        onStatus?.('disconnected');
        if (closed || !reconnect) return;
        timer = setTimeout(open, delay + Math.random() * 500);
        delay = Math.min(delay * 2, maxBackoffMs);
      };
      socket.onerror = () => {};   // onclose follows and handles the retry
    };
    open();

    return {
      subscribe(s) { const list = typeof s === 'string' ? [s] : s; list.forEach(x => wanted.add(x)); send({ action: 'subscribe', symbols: list }); },
      unsubscribe(s) { const list = typeof s === 'string' ? [s] : s; list.forEach(x => wanted.delete(x)); send({ action: 'unsubscribe', symbols: list }); },
      close() { closed = true; clearTimeout(timer); socket?.close(); },
    };
  }
}

/** Original name, kept so existing code keeps working. */
export const MarketClient = TickvaleClient;
