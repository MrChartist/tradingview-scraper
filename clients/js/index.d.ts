export interface Quote {
  symbol: string;
  status: 'ok' | 'error';
  error?: string;
  price: number | null;
  change: number | null;
  change_percent: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
  prev_close?: number | null;
  volume: number | null;
  bid: number | null;
  ask: number | null;
  currency: string | null;
  name: string | null;
  description: string | null;
  exchange: string | null;
  type: string | null;
  market_cap?: number | null;
  high_52w?: number | null;
  low_52w?: number | null;
  sector?: string | null;
  industry?: string | null;
  update_mode: string | null;
  /** true when the price is streaming in real time */
  realtime: boolean;
  /** true when the price is delayed; see delay_seconds */
  delayed: boolean;
  delay_seconds: number | null;
  time?: number | null;
}

export interface Candle { time: number; datetime: string; open: number; high: number; low: number; close: number; volume: number | null }
export interface SymbolHit { exchange: string; symbol: string; description: string; type: string; country: string | null; currency: string | null }
export interface NewsItem { id: string; title: string; provider: string; source: string; published_at: number; url: string }
export interface Condition { field: string; op: 'gt' | 'gte' | 'lt' | 'lte' | 'eq' | 'neq' | 'in' | 'between'; value: unknown }
export interface Meta { request_id: string; count?: number; not_found?: string[]; [key: string]: unknown }
export type Row = Record<string, any>;

export class MarketApiError extends Error {
  status: number | null; code: string; requestId: string | null; retryAfter: number | null;
}
export class AuthError extends MarketApiError {}
export class RateLimitError extends MarketApiError {}
export class NotFoundError extends MarketApiError {}
export class UpstreamError extends MarketApiError {}
export class ConnectionFailed extends MarketApiError {}

export interface ClientOptions {
  baseUrl?: string; apiKey?: string; timeoutMs?: number; maxRetries?: number;
  fetch?: typeof fetch; WebSocket?: any;
}
export interface StreamOptions {
  onQuote?: (quote: Quote) => void;
  onError?: (error: MarketApiError) => void;
  onStatus?: (status: 'connected' | 'disconnected') => void;
  reconnect?: boolean; maxBackoffMs?: number;
}
export interface LiveHandle { subscribe(symbols: string | string[]): void; unsubscribe(symbols: string | string[]): void; close(): void }

export class MarketClient {
  constructor(options?: ClientOptions);
  health(): Promise<{ status: string; version: string }>;
  status(): Promise<Row>;
  quotes(symbols: string | string[]): Promise<Quote[]>;
  quotes(symbols: string | string[], options: { withMeta: true }): Promise<{ data: Quote[]; meta: Meta }>;
  quote(symbol: string): Promise<Quote>;
  search(q: string, limit?: number): Promise<SymbolHit[]>;
  symbol(symbol: string): Promise<Row>;
  fundamentals(symbol: string): Promise<Row>;
  technicals(symbol: string, timeframe?: string): Promise<Row>;
  candles(symbol: string, options?: { timeframe?: string; limit?: number }): Promise<Candle[]>;
  news(symbol: string, options?: { limit?: number; language?: string }): Promise<NewsItem[]>;
  movers(options?: { market?: string; category?: string; limit?: number }): Promise<Row[]>;
  screener(options?: { market?: string; conditions?: Condition[]; columns?: string[] | null; sortBy?: string; sortOrder?: 'asc' | 'desc'; limit?: number; mainOnly?: boolean; withMeta?: boolean }): Promise<Row[]>;
  earnings(options?: { markets?: string | string[]; from?: string; to?: string; limit?: number }): Promise<Row[]>;
  dividends(options?: { markets?: string | string[]; from?: string; to?: string; limit?: number }): Promise<Row[]>;
  stream(symbols: string | string[], options?: StreamOptions): LiveHandle;
}
