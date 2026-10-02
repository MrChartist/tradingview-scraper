'use strict';

// ═══════════════════════════════════════════════════════════════════
//  HELPERS
// ═══════════════════════════════════════════════════════════════════
const $ = id => document.getElementById(id);
const val = id => $(id).value.trim();
const isNum = v => typeof v === 'number' && Number.isFinite(v);

function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function store(key, value) {
    try { localStorage.setItem(key, value); } catch { /* storage may be blocked */ }
}

/** GET JSON. Throws Error with the API's own message so users see why it failed. */
async function api(url) {
    let res;
    try { res = await fetch(url); }
    catch { throw new Error('Cannot reach the server. Check that it is running and your connection is up.'); }
    if (!res.ok) {
        let detail = `Request failed (${res.status})`;
        try { const j = await res.json(); if (j.detail) detail = typeof j.detail === 'string' ? j.detail : 'Invalid request'; } catch { /* keep default */ }
        throw new Error(detail);
    }
    return res.json();
}

function qs(params) {
    const p = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => { if (v !== '' && v != null) p.set(k, v); });
    return p.toString();
}

let toastTimer;
function toast(msg, type = 'info') {
    const t = $('toast');
    t.textContent = msg;
    t.className = `toast show ${type}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.className = 'toast'; }, 3800);
}

function setLoading(btnId, on) {
    const b = $(btnId);
    b.disabled = on;
    b.classList.toggle('is-loading', on);
}

function skeleton(n = 3) { return '<div class="skeleton"></div>'.repeat(n); }
function errorBox(msg) { return `<div class="error-box">${esc(msg)}</div>`; }

// ── Number formatting ──────────────────────────────────────────────
function compact(n) {
    const a = Math.abs(n);
    if (a >= 1e12) return (n / 1e12).toFixed(2) + 'T';
    if (a >= 1e9) return (n / 1e9).toFixed(2) + 'B';
    if (a >= 1e6) return (n / 1e6).toFixed(2) + 'M';
    if (a >= 1e5) return (n / 1e3).toFixed(1) + 'K';
    return null;
}
function price(n) {
    if (!isNum(n)) return '—';
    const a = Math.abs(n);
    const d = a >= 1000 ? 2 : a >= 1 ? 2 : a >= 0.01 ? 4 : 8;
    return n.toLocaleString('en-US', { minimumFractionDigits: Math.min(d, 2), maximumFractionDigits: d });
}
function pct(n) { return isNum(n) ? `${n > 0 ? '+' : ''}${n.toFixed(2)}%` : '—'; }
function plain(n) {
    if (!isNum(n)) return '—';
    const c = compact(n);
    if (c) return c;
    return Number.isInteger(n) ? n.toLocaleString('en-US') : n.toLocaleString('en-US', { maximumFractionDigits: 4 });
}
function badge(n) {
    if (!isNum(n)) return '—';
    return `<span class="badge ${n > 0 ? 'pos' : n < 0 ? 'neg' : ''}">${pct(n)}</span>`;
}

const LABELS = {
    close: 'Price', change: 'Change %', change_abs: 'Change', change_from_open: 'Change from open',
    market_cap_basic: 'Market cap (USD)', market_cap_calc: 'Market cap, calc.', market_cap_diluted_calc: 'Market cap, diluted',
    price_earnings_ttm: 'P/E (TTM)', earnings_per_share_basic_ttm: 'EPS (TTM)', earnings_per_share_diluted_ttm: 'EPS diluted (TTM)',
    price_52_week_high: '52-week high', price_52_week_low: '52-week low', 'Value.Traded': 'Value traded',
    'Recommend.All': 'Rating score', beta_1_year: 'Beta (1Y)', dividends_yield: 'Dividend yield',
    price_book_fq: 'Price / book', price_sales_ttm: 'Price / sales', debt_to_equity: 'Debt / equity',
    'Perf.W': 'Week', 'Perf.1M': '1 month', 'Perf.3M': '3 months', 'Perf.6M': '6 months', 'Perf.Y': '1 year', 'Perf.YTD': 'Year to date',
    'Volatility.D': 'Volatility (day)', 'Volatility.W': 'Volatility (week)', 'Volatility.M': 'Volatility (month)',
    shares_outstanding: 'Shares outstanding', shares_float: 'Free float', return_on_equity_fq: 'ROE', return_on_assets_fq: 'ROA',
    return_on_investment_ttm: 'ROI (TTM)', total_revenue: 'Revenue', net_income_fy: 'Net income (FY)',
    current_ratio_fq: 'Current ratio', quick_ratio_fq: 'Quick ratio',
};
function label(k) {
    return LABELS[k] || k.replace(/\[(\d+)\]/g, ' (prev $1)').replace(/[._]/g, ' ').replace(/\s+/g, ' ').trim();
}
const isPctKey = k => /^(change|change_from_open)$|^Perf\.|^Volatility\.|percent|margin|yield|^return_on|payout/i.test(k);
const isPriceKey = k => /^(close|open|high|low|price_52_week_(high|low)|change_abs)$/.test(k);

// ═══════════════════════════════════════════════════════════════════
//  THEME
// ═══════════════════════════════════════════════════════════════════
$('themeBtn').addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    store('theme', next);
    if (symState.ohlcv) drawChart();
});
$('helpBtn').addEventListener('click', () => $('helpDialog').showModal());

// ═══════════════════════════════════════════════════════════════════
//  SECTION NAVIGATION
// ═══════════════════════════════════════════════════════════════════
const MODES = ['symbol', 'movers', 'screener'];
function switchMode(mode, { updateHash = true } = {}) {
    if (!MODES.includes(mode)) mode = 'symbol';
    document.querySelectorAll('.mode-btn').forEach(b => {
        const on = b.dataset.mode === mode;
        b.classList.toggle('active', on);
        b.setAttribute('aria-selected', on);
    });
    MODES.forEach(m => {
        const s = $('mode-' + m);
        s.classList.toggle('active', m === mode);
        s.hidden = m !== mode;
    });
    if (updateHash && mode !== 'symbol') history.replaceState(null, '', '#' + mode);
    if (mode !== 'movers') stopAuto();
}
document.querySelectorAll('.mode-btn').forEach(b => b.addEventListener('click', () => switchMode(b.dataset.mode)));

document.addEventListener('keydown', e => {
    const tag = (e.target.tagName || '').toLowerCase();
    if (['input', 'select', 'textarea'].includes(tag) || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === '/') { e.preventDefault(); switchMode('symbol'); $('symbolInput').focus(); }
    else if (['1', '2', '3'].includes(e.key)) switchMode(MODES[+e.key - 1]);
});

// ═══════════════════════════════════════════════════════════════════
//  SORTABLE / FILTERABLE TABLE
// ═══════════════════════════════════════════════════════════════════
const tables = {};   // container id -> { rows, cols, sortKey, sortDir, filter }

const MOVER_COLS = [
    { key: 'symbol', label: 'Symbol', left: true, render: r => symbolCell(r) },
    { key: 'close', label: 'Price', fmt: price },
    { key: 'change', label: 'Change %', html: badge },
    { key: 'change_abs', label: 'Change', fmt: price, tone: true },
    { key: 'volume', label: 'Volume', fmt: plain },
    { key: 'market_cap_basic', label: 'Mkt cap (USD)', fmt: plain },
    { key: 'price_earnings_ttm', label: 'P/E', fmt: n => isNum(n) ? n.toFixed(1) : '—' },
];
const SCREENER_COLS = MOVER_COLS.concat([
    { key: 'Recommend.All', label: 'Rating', fmt: n => isNum(n) ? n.toFixed(2) : '—' },
]);

function symbolCell(r) {
    const sym = esc(r.symbol);
    const desc = r.description || r.name;
    return `<span class="sym">${sym}</span>${desc && desc !== r.name ? `<span class="sym-sub">${esc(desc)}</span>` : ''}`;
}

function renderTable(id, rows, cols, { onRowClick } = {}) {
    const c = $(id);
    if (!rows || !rows.length) {
        c.innerHTML = '<div class="no-data">No results. Try relaxing the filters or switching market.</div>';
        delete tables[id];
        return;
    }
    // Add any extra columns the first row carries (keeps CSV and table in step).
    tables[id] = { rows, cols, sortKey: null, sortDir: 'desc', filter: '', onRowClick };
    paintTable(id);
}

function paintTable(id) {
    const t = tables[id];
    const c = $(id);
    let rows = t.rows;
    if (t.filter) {
        const f = t.filter.toLowerCase();
        rows = rows.filter(r => `${r.symbol} ${r.name || ''} ${r.description || ''}`.toLowerCase().includes(f));
    }
    if (t.sortKey) {
        const k = t.sortKey, dir = t.sortDir === 'asc' ? 1 : -1;
        rows = [...rows].sort((a, b) => {
            const x = a[k], y = b[k];
            if (x == null && y == null) return 0;
            if (x == null) return 1;
            if (y == null) return -1;
            return (typeof x === 'number' ? x - y : String(x).localeCompare(String(y))) * dir;
        });
    }
    let h = '<div class="table-wrap"><table><thead><tr>';
    t.cols.forEach(col => {
        const sort = t.sortKey === col.key ? (t.sortDir === 'asc' ? 'ascending' : 'descending') : 'none';
        h += `<th class="${col.left ? 'left' : ''}" data-key="${esc(col.key)}" aria-sort="${sort}" tabindex="0">${esc(col.label)}</th>`;
    });
    h += '</tr></thead><tbody>';
    rows.forEach((r, i) => {
        h += `<tr class="${t.onRowClick ? 'clickable' : ''}" data-i="${t.rows.indexOf(r)}">`;
        t.cols.forEach(col => {
            const v = r[col.key];
            let cell;
            if (col.render) cell = col.render(r);
            else if (col.html) cell = col.html(v);
            else if (col.fmt) cell = esc(col.fmt(v));
            else cell = esc(v ?? '—');
            const tone = col.tone && isNum(v) ? (v > 0 ? ' pos' : v < 0 ? ' neg' : '') : '';
            h += `<td class="${col.left ? 'left' : 'num'}${tone}">${cell}</td>`;
        });
        h += '</tr>';
    });
    h += `</tbody></table></div><div class="row-count">${rows.length}${rows.length !== t.rows.length ? ` of ${t.rows.length}` : ''} rows</div>`;
    c.innerHTML = h;

    c.querySelectorAll('th').forEach(th => {
        const act = () => {
            const key = th.dataset.key;
            if (t.sortKey === key) t.sortDir = t.sortDir === 'desc' ? 'asc' : 'desc';
            else { t.sortKey = key; t.sortDir = key === 'symbol' ? 'asc' : 'desc'; }
            paintTable(id);
        };
        th.addEventListener('click', act);
        th.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); act(); } });
    });
    if (t.onRowClick) {
        c.querySelectorAll('tbody tr').forEach(tr =>
            tr.addEventListener('click', () => t.onRowClick(t.rows[+tr.dataset.i])));
    }
}

function bindTableFilter(inputId, tableId) {
    $(inputId).addEventListener('input', e => {
        if (!tables[tableId]) return;
        tables[tableId].filter = e.target.value.trim();
        paintTable(tableId);
    });
}
bindTableFilter('moversFilter', 'moversContent');
bindTableFilter('screenerFilter', 'screenerContent');

function openSymbol(row) {
    const [exchange, ticker] = String(row.symbol).split(':');
    if (!ticker) return;
    switchMode('symbol');
    $('symbolInput').value = `${exchange}:${ticker}`;
    symSelected = { exchange, ticker };
    $('searchForm').requestSubmit();
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function setDownloads(prefix, path, params) {
    ['Csv', 'Json'].forEach(f => {
        const a = $(prefix + f);
        const fmt = f.toLowerCase();
        a.href = `${path}?${qs({ ...params, fmt })}`;
        a.setAttribute('download', '');
    });
}

// ═══════════════════════════════════════════════════════════════════
//  SYMBOL LOOKUP
// ═══════════════════════════════════════════════════════════════════
const symState = { exchange: '', ticker: '', timeframe: '1d', candles: 150, activeTab: 'overview', ohlcvKey: '', ohlcv: null };
let symData = {};
let symSelected = null;

const QUICK = ['NSE:RELIANCE', 'NSE:TCS', 'NSE:HDFCBANK', 'NSE:INFY', 'NASDAQ:AAPL', 'NASDAQ:NVDA', 'BINANCE:BTCUSDT'];
$('quickChips').innerHTML = '<span class="chips-label">Try:</span>' +
    QUICK.map(s => `<button type="button" class="chip" data-sym="${s}">${s}</button>`).join('');
$('quickChips').addEventListener('click', e => {
    const b = e.target.closest('.chip'); if (!b) return;
    $('symbolInput').value = b.dataset.sym;
    symSelected = null;
    $('searchForm').requestSubmit();
});

// ── Autocomplete ───────────────────────────────────────────────────
const sgBox = $('suggestions');
let sgItems = [], sgIndex = -1, sgTimer, sgSeq = 0;

function closeSuggestions() {
    sgBox.hidden = true; sgIndex = -1;
    $('symbolInput').setAttribute('aria-expanded', 'false');
}
function paintSuggestions(items, emptyMsg) {
    sgItems = items;
    sgIndex = -1;
    sgBox.innerHTML = items.length
        ? items.map((r, i) => `<li role="option" id="sg-${i}" data-i="${i}"><div class="sg-main"><div class="sg-sym">${esc(r.exchange)}:${esc(r.symbol)}</div><div class="sg-desc">${esc(r.description || '')}</div></div><div class="sg-meta">${esc(r.type || '')}${r.country ? ' · ' + esc(r.country) : ''}</div></li>`).join('')
        : `<li class="sg-empty">${esc(emptyMsg)}</li>`;
    sgBox.hidden = false;
    $('symbolInput').setAttribute('aria-expanded', 'true');
}
function pickSuggestion(i) {
    const r = sgItems[i]; if (!r) return;
    symSelected = { exchange: r.exchange, ticker: r.symbol };
    $('symbolInput').value = `${r.exchange}:${r.symbol}`;
    closeSuggestions();
    $('searchForm').requestSubmit();
}

$('symbolInput').addEventListener('input', e => {
    symSelected = null;
    const q = e.target.value.trim();
    clearTimeout(sgTimer);
    if (q.length < 2) { closeSuggestions(); return; }
    sgTimer = setTimeout(async () => {
        const seq = ++sgSeq;
        try {
            const r = await api(`/api/search?${qs({ q, limit: 10 })}`);
            if (seq === sgSeq && document.activeElement === $('symbolInput')) paintSuggestions(r.data, 'No matches. Try another name or the EXCHANGE:TICKER format.');
        } catch { if (seq === sgSeq) paintSuggestions([], 'Search is unavailable. Type EXCHANGE:TICKER, e.g. NSE:RELIANCE.'); }
    }, 250);
});
$('symbolInput').addEventListener('keydown', e => {
    if (sgBox.hidden || !sgItems.length) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        sgIndex = (sgIndex + (e.key === 'ArrowDown' ? 1 : -1) + sgItems.length) % sgItems.length;
        sgBox.querySelectorAll('li').forEach((li, i) => li.setAttribute('aria-selected', i === sgIndex));
        sgBox.querySelector(`#sg-${sgIndex}`)?.scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'Enter' && sgIndex >= 0) { e.preventDefault(); pickSuggestion(sgIndex); }
    else if (e.key === 'Escape') closeSuggestions();
});
sgBox.addEventListener('mousedown', e => { const li = e.target.closest('li[data-i]'); if (li) { e.preventDefault(); pickSuggestion(+li.dataset.i); } });
document.addEventListener('click', e => { if (!e.target.closest('.search-field')) closeSuggestions(); });

/** Work out exchange + ticker from what the user typed or picked. */
async function resolveSymbol() {
    if (symSelected) return symSelected;
    const raw = val('symbolInput').toUpperCase().replace(/\s+/g, '');
    if (raw.includes(':')) { const [exchange, ticker] = raw.split(':'); return exchange && ticker ? { exchange, ticker } : null; }
    const r = await api(`/api/search?${qs({ q: raw, limit: 1 })}`);
    const first = r.data[0];
    return first ? { exchange: first.exchange, ticker: first.symbol } : null;
}

$('searchForm').addEventListener('submit', async e => {
    e.preventDefault();
    closeSuggestions();
    setLoading('searchBtn', true);
    try {
        const pick = await resolveSymbol();
        if (!pick) { toast('Symbol not found. Try a different name or EXCHANGE:TICKER.', 'error'); return; }
        Object.assign(symState, pick, {
            timeframe: val('timeframe'),
            candles: Math.min(5000, Math.max(5, parseInt(val('candles'), 10) || 150)),
            ohlcv: null, ohlcvKey: '',
        });
        $('symbolInput').value = `${pick.exchange}:${pick.ticker}`;
        history.replaceState(null, '', `#symbol=${pick.exchange}:${pick.ticker}`);

        $('symbolEmpty').hidden = true;
        $('symbolResults').hidden = false;
        $('symbolHead').innerHTML = '<div class="skeleton" style="height:48px;width:100%"></div>';
        ['overview', 'fundamentals', 'indicators'].forEach(id => { $(id).innerHTML = skeleton(); });
        $('ohlcv').innerHTML = '';

        const base = `${symState.exchange}/${symState.ticker}`;
        const [o, f, i] = await Promise.allSettled([
            api(`/api/overview/${base}`),
            api(`/api/fundamentals/${base}`),
            api(`/api/indicators/${base}?${qs({ timeframe: symState.timeframe })}`),
        ]);
        symData = {
            overview: o.status === 'fulfilled' ? o.value.data : null,
            fundamentals: f.status === 'fulfilled' ? f.value.data : null,
            indicators: i.status === 'fulfilled' ? i.value.data : null,
        };
        const err = [o, f, i].find(x => x.status === 'rejected');
        if (!symData.overview && !symData.fundamentals && !symData.indicators) {
            $('symbolHead').innerHTML = '';
            ['overview', 'fundamentals', 'indicators'].forEach(id => { $(id).innerHTML = errorBox(err ? err.reason.message : 'No data found.'); });
            toast(`No data for ${pick.exchange}:${pick.ticker}. Check the exchange and ticker.`, 'error');
            return;
        }
        renderSymbolHead();
        renderOverview();
        renderGrid('fundamentals', symData.fundamentals, 'Fundamental data is not available for this symbol.');
        renderGrid('indicators', symData.indicators, 'Indicator data is not available for this symbol.');
        selectTab(symState.activeTab, true);
    } catch (err) {
        toast(err.message, 'error');
    } finally {
        setLoading('searchBtn', false);
    }
});

function renderSymbolHead() {
    const d = symData.overview || {};
    const tvUrl = `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(symState.exchange + ':' + symState.ticker)}`;
    $('symbolHead').innerHTML = `
        <div><div class="sh-name">${esc(d.description || symState.ticker)}</div><div class="sh-sym">${esc(symState.exchange)}:${esc(symState.ticker)}${d.type ? ' · ' + esc(d.type) : ''}</div></div>
        <div class="sh-price">${isNum(d.close) ? price(d.close) : '—'}</div>
        <div>${badge(d.change)} <span class="sh-sub">${isNum(d.change_abs) ? (d.change_abs > 0 ? '+' : '') + price(d.change_abs) : ''}</span></div>
        <a class="sh-link" href="${tvUrl}" target="_blank" rel="noopener noreferrer">Open on TradingView &#8599;</a>`;
}

// ── Overview: grouped and price-action first ───────────────────────
const OVERVIEW_GROUPS = [
    ['Price action', ['open', 'high', 'low', 'close', 'change_from_open', 'volume', 'Value.Traded', 'price_52_week_high', 'price_52_week_low', 'Volatility.D', 'Volatility.W', 'Volatility.M', 'ATR']],
    ['Performance', ['Perf.W', 'Perf.1M', 'Perf.3M', 'Perf.6M', 'Perf.YTD', 'Perf.Y']],
    ['Valuation', ['market_cap_basic', 'price_earnings_ttm', 'earnings_per_share_basic_ttm', 'earnings_per_share_diluted_ttm', 'price_book_fq', 'price_sales_ttm', 'dividends_yield', 'beta_1_year']],
    ['Profile', ['sector', 'industry', 'country', 'exchange', 'type', 'subtype', 'employees']],
];
const INDICATOR_KEYS = new Set(['RSI', 'MACD.macd', 'MACD.signal', 'Stoch.K', 'Stoch.D', 'CCI20', 'ADX', 'Recommend.All']);

function renderOverview() {
    const d = symData.overview;
    const c = $('overview');
    if (!d) { c.innerHTML = errorBox('Overview is not available for this symbol.'); return; }

    let h = '';
    if (isNum(d.high) && isNum(d.low) && d.high > d.low && isNum(d.close)) h += rangeBar("Day's range", d.low, d.high, d.close);
    if (isNum(d.price_52_week_high) && isNum(d.price_52_week_low) && d.price_52_week_high > d.price_52_week_low && isNum(d.close))
        h += rangeBar('52-week range', d.price_52_week_low, d.price_52_week_high, d.close);

    const used = new Set(['close', 'change', 'change_abs', 'symbol', 'name', 'description']);
    OVERVIEW_GROUPS.forEach(([title, keys]) => {
        const cards = keys.filter(k => d[k] != null).map(k => { used.add(k); return card(k, d[k]); }).join('');
        if (cards) h += `<div class="group"><div class="group-title">${title}</div><div class="data-grid">${cards}</div></div>`;
    });
    const rest = Object.keys(d).filter(k => !used.has(k) && !INDICATOR_KEYS.has(k) && d[k] != null && typeof d[k] !== 'object');
    if (rest.length) h += `<div class="group"><div class="group-title">Other</div><div class="data-grid">${rest.map(k => card(k, d[k])).join('')}</div></div>`;
    c.innerHTML = h;
    applyGridFilter();
}

function rangeBar(title, lo, hi, cur) {
    const p = Math.min(100, Math.max(0, ((cur - lo) / (hi - lo)) * 100));
    return `<div class="range"><div class="range-title">${esc(title)}</div><div class="range-row"><span>${price(lo)}</span><span>${price(hi)}</span></div><div class="range-track"><div class="range-dot" style="left:${p}%"></div></div></div>`;
}

function card(k, v) {
    let text, cls = '';
    if (typeof v === 'number') {
        if (isPctKey(k)) { text = pct(v); cls = v > 0 ? 'pos' : v < 0 ? 'neg' : ''; if (!/^(change|Perf|change_from_open)/.test(k)) { text = v.toFixed(2) + '%'; cls = ''; } }
        else if (isPriceKey(k)) text = price(v);
        else text = plain(v);
    } else { text = String(v); cls = 'text'; }
    return `<div class="data-card" data-search="${esc((label(k) + ' ' + k).toLowerCase())}" title="Click to copy"><div class="data-label">${esc(label(k))}</div><div class="data-value ${cls}" data-raw="${esc(v)}">${esc(text)}</div></div>`;
}

function renderGrid(id, data, emptyMsg) {
    const c = $(id);
    if (!data) { c.innerHTML = errorBox(emptyMsg); return; }
    const entries = Object.entries(data).filter(([, v]) => v != null && typeof v !== 'object');
    if (!entries.length) { c.innerHTML = `<div class="no-data">${esc(emptyMsg)}</div>`; return; }
    c.innerHTML = `<div class="data-grid">${entries.map(([k, v]) => card(k, v)).join('')}</div>`;
    applyGridFilter();
}

// Click-to-copy on cards
document.addEventListener('click', async e => {
    const c = e.target.closest('.data-card'); if (!c) return;
    const raw = c.querySelector('.data-value')?.dataset.raw;
    try { await navigator.clipboard.writeText(raw); toast(`Copied ${raw}`, 'success'); } catch { /* clipboard not permitted */ }
});

function applyGridFilter() {
    const f = val('gridFilter').toLowerCase();
    const pane = $(symState.activeTab);
    pane.querySelectorAll('.data-card').forEach(c => { c.hidden = !!f && !c.dataset.search.includes(f); });
    pane.querySelectorAll('.group').forEach(g => { g.hidden = !!f && ![...g.querySelectorAll('.data-card')].some(c => !c.hidden); });
}
$('gridFilter').addEventListener('input', applyGridFilter);

// ── Tabs ───────────────────────────────────────────────────────────
document.querySelectorAll('#symbolTabs .tab-btn').forEach(b => b.addEventListener('click', () => selectTab(b.dataset.tab)));

function selectTab(tab, silent) {
    symState.activeTab = tab;
    document.querySelectorAll('#symbolTabs .tab-btn').forEach(b => {
        const on = b.dataset.tab === tab;
        b.classList.toggle('active', on);
        b.setAttribute('aria-selected', on);
    });
    document.querySelectorAll('.pane').forEach(p => p.classList.toggle('active', p.id === tab));
    $('gridFilter').hidden = tab === 'ohlcv';
    updateSymbolDownloads();
    if (tab === 'ohlcv') loadOHLCV();
    else applyGridFilter();
}

function updateSymbolDownloads() {
    const { exchange, ticker, timeframe, candles, activeTab: tab } = symState;
    if (!exchange) return;
    const params = tab === 'ohlcv' ? { timeframe, candles } : tab === 'indicators' ? { timeframe } : {};
    setDownloads('dl', `/api/download/${tab}/${exchange}/${ticker}`, params);
}

// ── OHLCV + candlestick chart ──────────────────────────────────────
async function loadOHLCV() {
    const key = `${symState.exchange}:${symState.ticker}:${symState.timeframe}:${symState.candles}`;
    if (symState.ohlcvKey === key && symState.ohlcv) return;
    const c = $('ohlcv');
    c.innerHTML = '<div class="info-banner">Connecting to TradingView for candle data. This can take 10 to 20 seconds.</div>' + skeleton(2);
    try {
        const res = await api(`/api/ohlcv/${symState.exchange}/${symState.ticker}?${qs({ timeframe: symState.timeframe, candles: symState.candles })}`);
        if (key !== `${symState.exchange}:${symState.ticker}:${symState.timeframe}:${symState.candles}`) return;
        symState.ohlcv = res.data;
        symState.ohlcvKey = key;
        renderOHLCV();
    } catch (err) {
        c.innerHTML = errorBox(err.message) + '<p class="hint" style="margin-top:10px;text-align:center">Switch tabs and come back to retry.</p>';
        symState.ohlcv = null; symState.ohlcvKey = '';
    }
}

function fmtTs(ts) {
    const d = new Date(ts * 1000);
    const intraday = !['1d', '1w', '1M'].includes(symState.timeframe);
    return d.toLocaleString('en-IN', intraday
        ? { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }
        : { day: '2-digit', month: 'short', year: 'numeric' });
}

function renderOHLCV() {
    const data = symState.ohlcv;
    const c = $('ohlcv');
    if (!data || !data.length) { c.innerHTML = '<div class="no-data">No candle data returned.</div>'; return; }
    const rows = [...data].reverse();
    const cols = [
        { key: 'timestamp', label: 'Time', left: true, fmt: n => isNum(n) ? fmtTs(n) : '—' },
        { key: 'open', label: 'Open', fmt: price }, { key: 'high', label: 'High', fmt: price },
        { key: 'low', label: 'Low', fmt: price }, { key: 'close', label: 'Close', fmt: price },
        { key: 'volume', label: 'Volume', fmt: plain },
    ];
    c.innerHTML = `<div class="info-banner">${data.length} candles · ${esc(symState.exchange)}:${esc(symState.ticker)} · ${esc(symState.timeframe)}. Newest first in the table.</div>
        <div class="chart-wrap"><canvas id="chart" aria-label="Candlestick chart"></canvas><div class="chart-tip" id="chartTip"></div></div>
        <div id="ohlcvTable"></div>`;
    renderTable('ohlcvTable', rows, cols);
    drawChart();
}

function cssVar(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }

function drawChart() {
    const cv = $('chart'); const data = symState.ohlcv;
    if (!cv || !data || !data.length) return;
    const dpr = window.devicePixelRatio || 1;
    const W = cv.clientWidth, H = cv.clientHeight;
    cv.width = W * dpr; cv.height = H * dpr;
    const g = cv.getContext('2d');
    g.scale(dpr, dpr);
    g.clearRect(0, 0, W, H);

    const pos = cssVar('--pos'), neg = cssVar('--neg'), dim = cssVar('--text-dim'), grid = cssVar('--border');
    const padL = 8, padR = 62, padT = 14, padB = 22;
    const volH = (H - padT - padB) * 0.18;
    const plotH = H - padT - padB - volH - 6;
    const plotW = W - padL - padR;
    const hi = Math.max(...data.map(d => d.high)), lo = Math.min(...data.map(d => d.low));
    const span = (hi - lo) || 1;
    const vmax = Math.max(...data.map(d => d.volume || 0)) || 1;
    const step = plotW / data.length;
    const bw = Math.max(1, Math.min(14, step * 0.7));
    const y = p => padT + (hi - p) / span * plotH;
    const x = i => padL + step * i + step / 2;

    g.font = '11px "JetBrains Mono", monospace';
    g.textBaseline = 'middle';
    for (let i = 0; i <= 4; i++) {
        const p = lo + span * i / 4, yy = y(p);
        g.strokeStyle = grid; g.globalAlpha = .6; g.lineWidth = 1;
        g.beginPath(); g.moveTo(padL, yy); g.lineTo(W - padR, yy); g.stroke();
        g.globalAlpha = 1; g.fillStyle = dim; g.textAlign = 'left';
        g.fillText(price(p), W - padR + 6, yy);
    }
    data.forEach((d, i) => {
        const up = d.close >= d.open, col = up ? pos : neg, xx = x(i);
        g.strokeStyle = col; g.fillStyle = col; g.lineWidth = 1;
        g.beginPath(); g.moveTo(xx, y(d.high)); g.lineTo(xx, y(d.low)); g.stroke();
        const top = y(Math.max(d.open, d.close)), h = Math.max(1, Math.abs(y(d.open) - y(d.close)));
        g.fillRect(xx - bw / 2, top, bw, h);
        const vh = (d.volume || 0) / vmax * volH;
        g.globalAlpha = .35; g.fillRect(xx - bw / 2, H - padB - vh, bw, vh); g.globalAlpha = 1;
    });
    g.fillStyle = dim; g.textAlign = 'center'; g.textBaseline = 'alphabetic';
    [0, Math.floor(data.length / 2), data.length - 1].forEach((i, n) => {
        g.textAlign = n === 0 ? 'left' : n === 2 ? 'right' : 'center';
        g.fillText(fmtTs(data[i].timestamp), n === 0 ? padL : n === 2 ? W - padR : x(i), H - 6);
    });

    cv.onmousemove = ev => {
        const r = cv.getBoundingClientRect();
        const i = Math.min(data.length - 1, Math.max(0, Math.floor((ev.clientX - r.left - padL) / step)));
        const d = data[i];
        $('chartTip').textContent = `${fmtTs(d.timestamp)}  O ${price(d.open)}  H ${price(d.high)}  L ${price(d.low)}  C ${price(d.close)}  V ${plain(d.volume)}`;
    };
    cv.onmouseleave = () => { $('chartTip').textContent = ''; };
}
let resizeTimer;
window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(drawChart, 120); });

// ═══════════════════════════════════════════════════════════════════
//  MARKET MOVERS
// ═══════════════════════════════════════════════════════════════════
const moversState = { market: 'stocks-india', category: 'gainers', limit: 25 };
let autoTimer = null;

function syncCategoryOptions() {
    const us = val('moversMarket') === 'stocks-usa';
    const stock = val('moversMarket').startsWith('stocks');
    $('moversCategory').querySelectorAll('option').forEach(o => {
        const usOnly = o.hasAttribute('data-us-only');
        const penny = o.value === 'penny-stocks';
        o.disabled = (usOnly && !us) || (penny && !stock);
        o.hidden = o.disabled;
    });
    if ($('moversCategory').selectedOptions[0]?.disabled) $('moversCategory').value = 'gainers';
}
$('moversMarket').addEventListener('change', syncCategoryOptions);
syncCategoryOptions();

function stopAuto() { clearInterval(autoTimer); autoTimer = null; }
$('moversAuto').addEventListener('change', e => {
    stopAuto();
    if (e.target.checked) { autoTimer = setInterval(() => fetchMovers(true), 60000); toast('Auto-refresh on (every 60 seconds)', 'info'); }
});

async function fetchMovers(quiet = false) {
    Object.assign(moversState, {
        market: val('moversMarket'), category: val('moversCategory'),
        limit: Math.min(100, Math.max(1, parseInt(val('moversLimit'), 10) || 25)),
    });
    if (!quiet) setLoading('moversBtn', true);
    try {
        const res = await api(`/api/movers?${qs(moversState)}`);
        $('moversResults').hidden = false;
        $('moversTitle').textContent = `${moversState.category.replace(/-/g, ' ')} · ${$('moversMarket').selectedOptions[0].textContent}`;
        $('moversFilter').value = '';
        renderTable('moversContent', res.data, MOVER_COLS, { onRowClick: openSymbol });
        setDownloads('movers', '/api/download/movers', moversState);
        if (!quiet) toast(`${res.data.length} rows loaded. Click a row to open the symbol.`, 'success');
    } catch (err) {
        if (!quiet) { $('moversResults').hidden = false; $('moversContent').innerHTML = errorBox(err.message); }
        toast(err.message, 'error');
    } finally {
        if (!quiet) setLoading('moversBtn', false);
    }
}
$('moversForm').addEventListener('submit', e => { e.preventDefault(); fetchMovers(); });

// ═══════════════════════════════════════════════════════════════════
//  SCREENER
// ═══════════════════════════════════════════════════════════════════
const SCREENER_FIELDS = {
    screenerMinPrice: 'min_price', screenerMaxPrice: 'max_price', screenerMinChange: 'min_change',
    screenerMaxChange: 'max_change', screenerMinVol: 'min_volume', screenerMinCap: 'min_market_cap',
};
const PRESETS = [
    { name: 'Active gainers', set: { screenerMinChange: 2, screenerMinVol: 500000, screenerSort: 'change', screenerOrder: 'desc' } },
    { name: 'Active losers', set: { screenerMaxChange: -2, screenerMinVol: 500000, screenerSort: 'change', screenerOrder: 'asc' } },
    { name: 'Volume leaders', set: { screenerSort: 'volume', screenerOrder: 'desc' } },
    { name: 'Large caps', set: { screenerMinCap: 10000000000, screenerSort: 'market_cap_basic', screenerOrder: 'desc' } },
    { name: 'Under 100, liquid', set: { screenerMaxPrice: 100, screenerMinVol: 1000000, screenerSort: 'volume', screenerOrder: 'desc' } },
];
$('screenerPresets').innerHTML = '<span class="chips-label">Presets:</span>' +
    PRESETS.map((p, i) => `<button type="button" class="chip" data-i="${i}">${p.name}</button>`).join('');
$('screenerPresets').addEventListener('click', e => {
    const b = e.target.closest('.chip'); if (!b) return;
    resetScreener(false);
    Object.entries(PRESETS[+b.dataset.i].set).forEach(([id, v]) => { $(id).value = v; });
    $('screenerForm').requestSubmit();
});
function resetScreener(clearResults = true) {
    Object.keys(SCREENER_FIELDS).forEach(id => { $(id).value = ''; });
    $('screenerSort').value = 'volume'; $('screenerOrder').value = 'desc'; $('screenerLimit').value = 25;
    if (clearResults) $('screenerResults').hidden = true;
}
$('screenerReset').addEventListener('click', () => resetScreener());

$('screenerForm').addEventListener('submit', async e => {
    e.preventDefault();
    const params = {
        market: val('screenerMarket'), sort_by: val('screenerSort'), sort_order: val('screenerOrder'),
        limit: Math.min(200, Math.max(1, parseInt(val('screenerLimit'), 10) || 25)),
    };
    Object.entries(SCREENER_FIELDS).forEach(([id, key]) => { if (val(id) !== '') params[key] = val(id); });
    setLoading('screenerBtn', true);
    try {
        const res = await api(`/api/screener?${qs(params)}`);
        $('screenerResults').hidden = false;
        const total = res.totalCount ?? res.total;
        $('screenerTitle').textContent = `${$('screenerMarket').selectedOptions[0].textContent} · ${res.data.length}${total ? ' of ' + Number(total).toLocaleString('en-US') : ''} matches`;
        $('screenerFilter').value = '';
        renderTable('screenerContent', res.data, SCREENER_COLS, { onRowClick: openSymbol });
        setDownloads('screener', '/api/download/screener', params);
        toast(`${res.data.length} results. Click a row to open the symbol.`, 'success');
    } catch (err) {
        $('screenerResults').hidden = false;
        $('screenerContent').innerHTML = errorBox(err.message);
        toast(err.message, 'error');
    } finally {
        setLoading('screenerBtn', false);
    }
});

// ═══════════════════════════════════════════════════════════════════
//  INIT (restore from URL hash)
// ═══════════════════════════════════════════════════════════════════
(function init() {
    const hash = decodeURIComponent(location.hash.replace(/^#/, ''));
    if (hash.startsWith('symbol=')) {
        $('symbolInput').value = hash.slice(7).toUpperCase();
        $('searchForm').requestSubmit();
    } else if (MODES.includes(hash)) {
        switchMode(hash, { updateHash: false });
    }
})();
