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

const FRIENDLY = {
    404: "We couldn't find that. Try searching by the company's name.",
    422: 'Something in the form looks off. Please check the numbers and try again.',
    429: "You're going a bit fast. Wait a few seconds and try again.",
    502: 'Our market data source is having trouble. Please try again in a moment.',
    503: 'The service is busy. Please try again shortly.',
};

/** GET JSON. Errors carry a plain-English message a non-technical person can act on. */
async function api(url) {
    let res;
    try { res = await fetch(url); }
    catch { throw new Error("Can't reach the server. Check your internet connection and try again."); }
    if (!res.ok) {
        let detail = FRIENDLY[res.status] || 'Something went wrong. Please try again.';
        try {
            const j = await res.json();
            // The server's own words are used when they are already plain (400-style messages).
            if (res.status === 400 && typeof j.detail === 'string') detail = j.detail;
        } catch { /* keep the friendly default */ }
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

// ── Number formatting (currency aware; rupees use Lakh / Crore) ─────
const SYMBOLS = { INR: '₹', USD: '$', GBP: '£', EUR: '€', CAD: 'C$', AUD: 'A$', JPY: '¥', CHF: 'CHF ', AED: 'AED ' };
const sym = ccy => (ccy && SYMBOLS[ccy]) || '';
const locale = ccy => (ccy === 'INR' ? 'en-IN' : 'en-US');

function price(n, ccy, withSymbol = false) {
    if (!isNum(n)) return '—';
    const a = Math.abs(n);
    const d = a >= 1 ? 2 : a >= 0.01 ? 4 : 8;
    const body = Math.abs(n).toLocaleString(locale(ccy), { minimumFractionDigits: Math.min(d, 2), maximumFractionDigits: d });
    return `${n < 0 ? '-' : ''}${withSymbol ? sym(ccy) : ''}${body}`;
}
function pct(n) { return isNum(n) ? `${n > 0 ? '+' : ''}${n.toFixed(2)}%` : '—'; }

/** Large amounts. INR: ₹15.78 L Cr / ₹7,622 Cr / ₹4.50 L. Others: $4.84T / $1.20B / $350.00M. */
function money(n, ccy) {
    if (!isNum(n)) return '—';
    const a = Math.abs(n), neg = n < 0 ? '-' : '', s = sym(ccy);
    if (ccy === 'INR') {
        if (a >= 1e12) return `${neg}${s}${(a / 1e12).toFixed(2)} L Cr`;
        if (a >= 1e7) return `${neg}${s}${(a / 1e7).toLocaleString('en-IN', { maximumFractionDigits: a >= 1e9 ? 0 : 2 })} Cr`;
        if (a >= 1e5) return `${neg}${s}${(a / 1e5).toFixed(2)} L`;
        return `${neg}${s}${a.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
    }
    const u = a >= 1e12 ? [1e12, 'T'] : a >= 1e9 ? [1e9, 'B'] : a >= 1e6 ? [1e6, 'M'] : null;
    return u ? `${neg}${s}${(a / u[0]).toFixed(2)}${u[1]}` : `${neg}${s}${a.toLocaleString('en-US', { maximumFractionDigits: 2 })}`;
}

/** Share volume. India reads Lakh / Crore; elsewhere K / M / B. */
function volume(n, ccy) {
    if (!isNum(n)) return '—';
    const a = Math.abs(n);
    if (ccy === 'INR') {
        if (a >= 1e7) return `${(n / 1e7).toFixed(2)} Cr`;
        if (a >= 1e5) return `${(n / 1e5).toFixed(2)} L`;
        return n.toLocaleString('en-IN', { maximumFractionDigits: 0 });
    }
    if (a >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
    if (a >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
    if (a >= 1e4) return `${(n / 1e3).toFixed(1)}K`;
    return n.toLocaleString('en-US', { maximumFractionDigits: 0 });
}
function plain(n) {
    if (!isNum(n)) return '—';
    const a = Math.abs(n);
    if (a >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
    if (a >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
    if (Number.isInteger(n)) return n.toLocaleString('en-US');
    return n.toLocaleString('en-US', { maximumFractionDigits: a >= 1 ? 2 : 4 });
}
function badge(n) {
    if (!isNum(n)) return '—';
    return `<span class="badge ${n > 0 ? 'pos' : n < 0 ? 'neg' : ''}">${pct(n)}</span>`;
}

const LABELS = {
    close: 'Price', change: 'Move today', change_abs: 'Change today', change_from_open: 'Move since the open',
    open: 'Opened at', high: "Today's high", low: "Today's low", volume: 'Shares traded today',
    market_cap_basic: 'Company size (market cap)', market_cap_calc: 'Company size (calculated)', market_cap_diluted_calc: 'Company size (diluted)',
    price_earnings_ttm: 'P/E ratio', earnings_per_share_basic_ttm: 'Profit per share (last 12 months)',
    earnings_per_share_diluted_ttm: 'Profit per share, diluted', price_52_week_high: '1-year high', price_52_week_low: '1-year low',
    'Value.Traded': 'Money traded today', 'Recommend.All': 'Overall signal score', beta_1_year: 'Beta (1 year)',
    dividends_yield: 'Dividend yield', price_book_fq: 'Price to book value', price_sales_ttm: 'Price to sales',
    debt_to_equity: 'Debt compared with equity',
    'Perf.W': 'Past week', 'Perf.1M': 'Past month', 'Perf.3M': 'Past 3 months', 'Perf.6M': 'Past 6 months', 'Perf.Y': 'Past year', 'Perf.YTD': 'Since January',
    'Volatility.D': 'Daily swings', 'Volatility.W': 'Weekly swings', 'Volatility.M': 'Monthly swings', ATR: 'Average daily range',
    shares_outstanding: 'Shares in existence', shares_float: 'Shares available to trade', return_on_equity_fq: 'Return on equity',
    return_on_assets_fq: 'Return on assets', return_on_investment_ttm: 'Return on investment', total_revenue: 'Yearly sales (revenue)',
    net_income_fy: 'Yearly profit (net income)', current_ratio_fq: 'Current ratio', quick_ratio_fq: 'Quick ratio',
    sector: 'Sector', industry: 'Industry', country: 'Country', exchange: 'Exchange', type: 'Type', subtype: 'Sub-type',
    employees: 'Employees',
};
function label(k) {
    if (LABELS[k]) return LABELS[k];
    const t = k.replace(/\[(\d+)\]/g, ' (prev $1)').replace(/[._]/g, ' ').replace(/\s+/g, ' ').trim();
    return t.charAt(0).toUpperCase() + t.slice(1);
}
const MONEY_RE = /market_cap|earnings_per_share|^eps|per_share|revenue|income|profit|^cash_|debt|assets|ebitda|enterprise_value|free_cash_flow|^Value\.Traded$|ebit$/i;
const NOT_MONEY_RE = /margin|ratio|_to_|return_on|percent|yield|payout|growth|^debt_to|^current_|^quick_/i;
const isMoneyKey = k => MONEY_RE.test(k) && !NOT_MONEY_RE.test(k);
const isPerShareKey = k => /per_share|earnings_per_share|^eps|basic_eps/i.test(k);
const isPctKey = k => /^(change|change_from_open)$|^Perf\.|^Volatility\.|percent|margin|yield|^return_on|payout/i.test(k);
const isPriceKey = k => /^(close|open|high|low|price_52_week_(high|low)|change_abs)$/.test(k);

// ═══════════════════════════════════════════════════════════════════
//  THEME
// ═══════════════════════════════════════════════════════════════════
$('themeBtn').addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    store('theme', next);
    if (symState.ohlcv) drawChart(false);
});

// ═══════════════════════════════════════════════════════════════════
//  HELP: welcome card + glossary (text comes from the server, one source of truth)
// ═══════════════════════════════════════════════════════════════════
let GLOSS = { terms: {}, field_terms: {}, catalog: { markets: [], categories: {} } };
const glossLoaded = api('/api/glossary').then(g => { GLOSS = g; paintGlossary(); }).catch(() => {});

function paintGlossary() {
    const f = ($('glossarySearch').value || '').trim().toLowerCase();
    const items = Object.values(GLOSS.terms).filter(t => !f || (t.title + ' ' + t.plain).toLowerCase().includes(f));
    $('glossaryList').innerHTML = items.length
        ? items.map(t => `<dt>${esc(t.title)}</dt><dd>${esc(t.plain)}</dd>`).join('')
        : '<p class="hint">No word matches that. Try another spelling.</p>';
}
$('glossaryBtn').addEventListener('click', () => { paintGlossary(); $('glossaryDialog').showModal(); $('glossarySearch').focus(); });
$('glossarySearch').addEventListener('input', paintGlossary);

try { if (!localStorage.getItem('welcomed')) $('welcome').hidden = false; } catch { $('welcome').hidden = false; }
$('welcomeClose').addEventListener('click', () => { $('welcome').hidden = true; store('welcomed', '1'); });

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
    { key: 'symbol', label: 'Company', left: true, render: r => symbolCell(r) },
    { key: 'close', label: 'Price', tip: 'price', fmtRow: r => price(r.close, r.currency) },
    { key: 'change', label: 'Today', tip: 'change', html: badge },
    { key: 'change_abs', label: 'Change', tip: 'change', fmtRow: r => price(r.change_abs, r.currency), tone: true },
    { key: 'volume', label: 'Shares traded', tip: 'volume', fmtRow: r => volume(r.volume, r.currency) },
    { key: 'market_cap_basic', label: 'Company size', tip: 'market_cap', fmtRow: r => money(r.market_cap_basic, r.currency) },
    { key: 'price_earnings_ttm', label: 'P/E', tip: 'pe', fmt: n => isNum(n) ? n.toFixed(1) : '—' },
];
const SCREENER_COLS = MOVER_COLS;
const termTip = id => (GLOSS.terms[id] ? `${GLOSS.terms[id].title}: ${GLOSS.terms[id].plain}` : '');

function symbolCell(r) {
    const desc = r.description || r.name || r.symbol;
    return `<span class="sym">${esc(desc)}</span><span class="sym-sub">${esc(r.symbol)}</span>`;
}

function renderTable(id, rows, cols, { onRowClick } = {}) {
    const c = $(id);
    if (!rows || !rows.length) {
        c.innerHTML = '<div class="no-data"><strong>Nothing matches right now.</strong><br>Try loosening a rule, picking another market, or coming back when the market is open.</div>';
        delete tables[id];
        return;
    }
    // Add any extra columns the first row carries (keeps CSV and table in step).
    tables[id] = { rows, cols, sortKey: null, sortDir: 'desc', filter: '', onRowClick, fresh: true };
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
        h += `<th class="${col.left ? 'left' : ''}" data-key="${esc(col.key)}" aria-sort="${sort}" tabindex="0" title="${esc(termTip(col.tip) || 'Click to sort')}">${esc(col.label)}</th>`;
    });
    h += '</tr></thead><tbody>';
    rows.forEach((r, i) => {
        const enter = t.fresh && i < 24 ? ` enter" style="--i:${i}` : '';
        h += `<tr class="${t.onRowClick ? 'clickable' : ''}${enter}" data-i="${t.rows.indexOf(r)}">`;
        t.cols.forEach(col => {
            const v = r[col.key];
            let cell;
            if (col.render) cell = col.render(r);
            else if (col.html) cell = col.html(v);
            else if (col.fmtRow) cell = esc(col.fmtRow(r));
            else if (col.fmt) cell = esc(col.fmt(v));
            else cell = esc(v ?? '—');
            const tone = col.tone && isNum(v) ? (v > 0 ? ' pos' : v < 0 ? ' neg' : '') : '';
            h += `<td class="${col.left ? 'left' : 'num'}${tone}">${cell}</td>`;
        });
        h += '</tr>';
    });
    h += `</tbody></table></div><div class="row-count">${rows.length}${rows.length !== t.rows.length ? ` of ${t.rows.length}` : ''} ${rows.length === 1 ? 'stock' : 'stocks'}${t.onRowClick ? '. Tap a row for the full picture.' : ''}</div>`;
    c.innerHTML = h;
    t.fresh = false;

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

const QUICK = ['Reliance', 'TCS', 'HDFC Bank', 'Infosys', 'Apple', 'Tesla', 'Bitcoin'];
$('quickChips').innerHTML = '<span class="chips-label">Popular:</span>' +
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

const KINDS = { stock: 'Stock', fund: 'Fund', etf: 'Fund', index: 'Index', crypto: 'Crypto', spot: 'Crypto', swap: 'Crypto', futures: 'Futures', forex: 'Currency', cfd: 'CFD', dr: 'Stock', bond: 'Bond' };
const kindName = t => KINDS[t] || (t ? t.charAt(0).toUpperCase() + t.slice(1) : '');

function closeSuggestions() {
    sgBox.hidden = true; sgIndex = -1;
    $('symbolInput').setAttribute('aria-expanded', 'false');
}
function paintSuggestions(items, emptyMsg) {
    sgItems = items;
    sgIndex = -1;
    sgBox.innerHTML = items.length
        ? items.map((r, i) => `<li role="option" id="sg-${i}" data-i="${i}"><div class="sg-main"><div class="sg-sym">${esc(r.description || r.symbol)}</div><div class="sg-desc">${esc(r.exchange)}:${esc(r.symbol)}</div></div><div class="sg-meta">${esc(kindName(r.type))}${r.country ? ' · ' + esc(r.country) : ''}</div></li>`).join('')
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
            if (seq === sgSeq && document.activeElement === $('symbolInput')) paintSuggestions(r.data, 'No match yet. Try the company\'s short name, like "Infosys".');
        } catch { if (seq === sgSeq) paintSuggestions([], 'Search is not available right now. You can still type a code like NSE:RELIANCE.'); }
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

/** Work out exchange + ticker from what the user typed or picked. Also returns close alternatives. */
async function resolveSymbol() {
    if (symSelected) return { ...symSelected, alternatives: [] };
    const raw = val('symbolInput');
    const compact = raw.toUpperCase().replace(/\s+/g, '');
    if (compact.includes(':')) { const [exchange, ticker] = compact.split(':'); return exchange && ticker ? { exchange, ticker, alternatives: [] } : null; }
    const r = await api(`/api/resolve?${qs({ q: raw })}`);
    const best = r.data.best;
    return best ? { exchange: best.exchange, ticker: best.symbol, alternatives: r.data.alternatives || [], typed: raw } : null;
}

function paintDidYouMean(pick) {
    const box = $('didYouMean');
    const alts = (pick.alternatives || []).filter(a => a.type !== 'futures').slice(0, 4);
    if (!pick.typed || !alts.length) { box.hidden = true; return; }
    box.innerHTML = `Showing <strong>${esc(pick.exchange)}:${esc(pick.ticker)}</strong> for "${esc(pick.typed)}". Meant something else? ` +
        alts.map(a => `<button type="button" class="chip" data-ex="${esc(a.exchange)}" data-t="${esc(a.symbol)}" title="${esc(a.description || '')}">${esc(a.description ? a.description.slice(0, 28) : a.full_symbol)} <small>${esc(a.full_symbol)}</small></button>`).join(' ');
    box.hidden = false;
}
$('didYouMean').addEventListener('click', e => {
    const b = e.target.closest('.chip'); if (!b) return;
    symSelected = { exchange: b.dataset.ex, ticker: b.dataset.t };
    $('symbolInput').value = `${b.dataset.ex}:${b.dataset.t}`;
    $('searchForm').requestSubmit();
});

$('searchForm').addEventListener('submit', async e => {
    e.preventDefault();
    closeSuggestions();
    setLoading('searchBtn', true);
    try {
        const pick = await resolveSymbol();
        if (!pick) { toast("We couldn't find that. Try the company's short name, like Infosys.", 'error'); return; }
        Object.assign(symState, pick, {
            timeframe: val('timeframe'),
            candles: Math.min(5000, Math.max(5, parseInt(val('candles'), 10) || 150)),
            ohlcv: null, ohlcvKey: '',
        });
        $('symbolInput').value = `${pick.exchange}:${pick.ticker}`;
        paintDidYouMean(pick);
        history.replaceState(null, '', `#symbol=${pick.exchange}:${pick.ticker}`);

        $('symbolEmpty').hidden = true;
        $('symbolResults').hidden = false;
        $('symbolHead').innerHTML = '<div class="skeleton" style="height:48px;width:100%"></div>';
        ['overview', 'fundamentals', 'indicators'].forEach(id => { $(id).innerHTML = skeleton(); });
        $('ohlcv').innerHTML = '';

        const base = `${symState.exchange}/${symState.ticker}`;
        await glossLoaded;
        const [o, f, i, qt] = await Promise.allSettled([
            api(`/api/overview/${base}`),
            api(`/api/fundamentals/${base}`),
            api(`/api/indicators/${base}?${qs({ timeframe: symState.timeframe })}`),
            api(`/api/quote/${base}`),
        ]);
        symData = {
            quote: qt.status === 'fulfilled' ? qt.value.data : null,
            overview: o.status === 'fulfilled' ? o.value.data : null,
            fundamentals: f.status === 'fulfilled' ? f.value.data : null,
            indicators: i.status === 'fulfilled' ? i.value.data : null,
        };
        const err = [o, f, i].find(x => x.status === 'rejected');
        if (!symData.overview && !symData.fundamentals && !symData.indicators) {
            $('symbolHead').innerHTML = '';
            ['overview', 'fundamentals', 'indicators'].forEach(id => { $(id).innerHTML = errorBox(err ? err.reason.message : 'No data found.'); });
            toast(`We couldn't load ${pick.exchange}:${pick.ticker}. Try another name.`, 'error');
            return;
        }
        renderSymbolHead();
        renderOverview();
        renderGrid('fundamentals', symData.fundamentals, "Company numbers aren't available for this one. That's normal for coins, indexes and some funds.");
        renderGrid('indicators', symData.indicators, "Advanced readings aren't available for this one.");
        selectTab(symState.activeTab, true);
    } catch (err) {
        toast(err.message, 'error');
    } finally {
        setLoading('searchBtn', false);
    }
});

const priceCcy = () => symData.overview?.price_currency || symData.overview?.currency || 'USD';
const fundCcy = () => symData.overview?.currency || symData.fundamentals?.currency || 'USD';

function freshnessTag(q) {
    if (!q) return '';
    if (q.realtime) return '<span class="tag tag-live" title="Updates the moment trades happen">Live</span>';
    if (q.delayed) return `<span class="tag tag-delayed" title="${esc(termTip('delayed'))}">Delayed ${q.delay_seconds ? Math.round(q.delay_seconds / 60) + ' min' : ''}</span>`;
    return `<span class="tag" title="${esc(termTip('delayed'))}">${esc(q.freshness || '')}</span>`;
}

function renderSymbolHead() {
    const d = symData.overview || {};
    const q = symData.quote;
    const tvUrl = `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(symState.exchange + ':' + symState.ticker)}`;
    $('symbolHead').innerHTML = `
        <div><div class="sh-name">${esc(d.description || q?.description || symState.ticker)}</div><div class="sh-sym">${esc(symState.exchange)}:${esc(symState.ticker)}${d.type ? ' · ' + esc(kindName(d.type)) : ''}</div></div>
        <div class="sh-price">${isNum(d.close) ? price(d.close, priceCcy(), true) : '—'}</div>
        <div>${badge(d.change)} <span class="sh-sub">${isNum(d.change_abs) ? (d.change_abs > 0 ? '+' : '') + price(d.change_abs, priceCcy()) : ''} today</span> ${freshnessTag(q)}</div>
        <a class="sh-link" href="${tvUrl}" target="_blank" rel="noopener noreferrer">See on TradingView &#8599;</a>`;
}

/** A few plain sentences that say what the numbers mean. */
function plainSummary(d, q) {
    const name = d.description || symState.ticker, ccy = priceCcy(), out = [];
    if (isNum(d.close)) {
        let move = '';
        if (isNum(d.change)) {
            move = d.change === 0 ? ", unchanged from yesterday's close"
                : `, ${d.change > 0 ? 'up' : 'down'} ${Math.abs(d.change).toFixed(2)}% from yesterday's close`;
        }
        out.push(`${name} last traded at <strong>${price(d.close, ccy, true)}</strong>${move}.`);
    }
    if (q) {
        if (q.realtime) out.push('This price is live.');
        else if (q.delayed) out.push(`This price is about <strong>${q.delay_seconds ? Math.round(q.delay_seconds / 60) : 15} minutes behind</strong> the market. That is normal for free data.`);
    }
    const hi = d.price_52_week_high, lo = d.price_52_week_low, c = d.close;
    if (isNum(hi) && isNum(lo) && isNum(c) && hi > lo) {
        const p = (c - lo) / (hi - lo);
        const where = p < 0.2 ? 'near the bottom of' : p > 0.8 ? 'near the top of' : 'in the middle of';
        out.push(`Over the past year it has ranged from ${price(lo, ccy, true)} to ${price(hi, ccy, true)}. Today it is <strong>${where} that range</strong>, ${(((hi - c) / hi) * 100).toFixed(0)}% below its high.`);
    }
    if (isNum(d.volume) && d.volume > 0) out.push(`<strong>${volume(d.volume, ccy)}</strong> shares have changed hands today.`);
    if (isNum(d.market_cap_basic)) out.push(`The whole company is valued at <strong>${money(d.market_cap_basic, fundCcy())}</strong> (its "market cap").`);
    if (isNum(d.price_earnings_ttm) && d.price_earnings_ttm > 0) out.push(`Its P/E is ${d.price_earnings_ttm.toFixed(1)}: investors are paying about ${sym(fundCcy())}${d.price_earnings_ttm.toFixed(0)} for every ${sym(fundCcy())}1 of yearly profit.`);
    if (!out.length) return '';
    return `<div class="plain-summary"><div class="group-title">In plain words</div><ul>${out.map(x => `<li>${x}</li>`).join('')}</ul><p class="hint">This is information, not advice. Tap any number below to see what it means.</p></div>`;
}

// ── Overview: grouped and price-action first ───────────────────────
const OVERVIEW_GROUPS = [
    ['Today', ['open', 'high', 'low', 'close', 'change_from_open', 'volume', 'Value.Traded', 'Volatility.D']],
    ['How it has done', ['Perf.W', 'Perf.1M', 'Perf.3M', 'Perf.6M', 'Perf.YTD', 'Perf.Y', 'price_52_week_high', 'price_52_week_low', 'Volatility.W', 'Volatility.M', 'ATR']],
    ['Size and value', ['market_cap_basic', 'price_earnings_ttm', 'earnings_per_share_basic_ttm', 'earnings_per_share_diluted_ttm', 'price_book_fq', 'price_sales_ttm', 'dividends_yield', 'beta_1_year']],
    ['About the company', ['sector', 'industry', 'country', 'exchange', 'type', 'subtype', 'employees']],
];
const INDICATOR_KEYS = new Set(['RSI', 'MACD.macd', 'MACD.signal', 'Stoch.K', 'Stoch.D', 'CCI20', 'ADX', 'Recommend.All']);

function renderOverview() {
    const d = symData.overview;
    const c = $('overview');
    if (!d) { c.innerHTML = errorBox("We couldn't load the summary. Please try again."); return; }

    let h = plainSummary(d, symData.quote);
    if (isNum(d.high) && isNum(d.low) && d.high > d.low && isNum(d.close)) h += rangeBar("Today's range (lowest to highest)", d.low, d.high, d.close);
    if (isNum(d.price_52_week_high) && isNum(d.price_52_week_low) && d.price_52_week_high > d.price_52_week_low && isNum(d.close))
        h += rangeBar('Past year (lowest to highest)', d.price_52_week_low, d.price_52_week_high, d.close);

    const used = new Set(['close', 'change', 'change_abs', 'symbol', 'name', 'description']);
    OVERVIEW_GROUPS.forEach(([title, keys]) => {
        const cards = keys.filter(k => d[k] != null).map(k => { used.add(k); return card(k, d[k]); }).join('');
        if (cards) h += `<div class="group"><div class="group-title">${title}</div><div class="data-grid">${cards}</div></div>`;
    });
    const rest = Object.keys(d).filter(k => !used.has(k) && k !== 'currency' && k !== 'price_currency' && !INDICATOR_KEYS.has(k) && d[k] != null && typeof d[k] !== 'object');
    if (rest.length) h += `<div class="group"><div class="group-title">More details</div><div class="data-grid">${rest.map(k => card(k, d[k])).join('')}</div></div>`;
    c.innerHTML = h;
    applyGridFilter();
}

function rangeBar(title, lo, hi, cur) {
    const p = Math.min(100, Math.max(0, ((cur - lo) / (hi - lo)) * 100));
    return `<div class="range"><div class="range-title">${esc(title)}</div><div class="range-row"><span>${price(lo, priceCcy(), true)}</span><span>${price(hi, priceCcy(), true)}</span></div><div class="range-track"><div class="range-dot" style="left:${p}%"></div></div></div>`;
}

function card(k, v) {
    let text, cls = '';
    if (typeof v === 'number') {
        if (isPctKey(k)) { text = pct(v); cls = v > 0 ? 'pos' : v < 0 ? 'neg' : ''; if (!/^(change|Perf|change_from_open)/.test(k)) { text = v.toFixed(2) + '%'; cls = ''; } }
        else if (k === 'volume') text = volume(v, priceCcy());
        else if (isPriceKey(k)) text = price(v, priceCcy(), true);
        else if (isPerShareKey(k)) text = price(v, fundCcy(), true);
        else if (isMoneyKey(k)) text = money(v, k === 'Value.Traded' ? priceCcy() : fundCcy());
        else text = plain(v);
    } else { text = String(v); cls = 'text'; }
    const term = GLOSS.field_terms[k], note = term && GLOSS.terms[term] ? GLOSS.terms[term].plain : '';
    const search = esc((label(k) + ' ' + k + ' ' + (note ? GLOSS.terms[term].title : '')).toLowerCase());
    const attrs = note ? ' role="button" tabindex="0" aria-expanded="false" title="Tap to see what this means"' : '';
    return `<div class="data-card${note ? ' explainable' : ''}" data-search="${search}"${attrs}><div class="data-label">${esc(label(k))}${note ? ' <span class="info" aria-hidden="true">i</span>' : ''}</div><div class="data-value ${cls}">${esc(text)}</div>${note ? `<div class="data-note" hidden>${esc(note)}</div>` : ''}</div>`;
}

function renderGrid(id, data, emptyMsg) {
    const c = $(id);
    if (!data) { c.innerHTML = errorBox(emptyMsg); return; }
    const entries = Object.entries(data).filter(([k, v]) => v != null && typeof v !== 'object' && k !== 'currency' && k !== 'price_currency');
    if (!entries.length) { c.innerHTML = `<div class="no-data">${esc(emptyMsg)}</div>`; return; }
    c.innerHTML = (id === 'indicators' ? '<p class="info-banner">Advanced: these readings are calculated from past prices and are mainly used by traders. If you are new, you can skip this tab.</p>' : '') +
        `<div class="data-grid">${entries.map(([k, v]) => card(k, v)).join('')}</div>`;
    applyGridFilter();
}

// Tap a number to see what it means
function toggleCard(c) {
    const note = c.querySelector('.data-note');
    if (!note) return;
    const open = note.hidden;
    note.hidden = !open;
    c.setAttribute('aria-expanded', open);
    c.classList.toggle('open', open);
}
document.addEventListener('click', e => { const c = e.target.closest('.data-card.explainable'); if (c) toggleCard(c); });
document.addEventListener('keydown', e => {
    if ((e.key === 'Enter' || e.key === ' ') && e.target.matches?.('.data-card.explainable')) { e.preventDefault(); toggleCard(e.target); }
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
    c.innerHTML = '<div class="info-banner">Loading the price history. This can take 10 to 20 seconds.</div>' + skeleton(2);
    try {
        const res = await api(`/api/ohlcv/${symState.exchange}/${symState.ticker}?${qs({ timeframe: symState.timeframe, candles: symState.candles })}`);
        if (key !== `${symState.exchange}:${symState.ticker}:${symState.timeframe}:${symState.candles}`) return;
        symState.ohlcv = res.data;
        symState.ohlcvKey = key;
        renderOHLCV();
    } catch (err) {
        c.innerHTML = errorBox(err.message) + '<p class="hint" style="margin-top:10px;text-align:center">Switch to another tab and back to try again.</p>';
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

const TF_NAMES = { '1m': 'minute', '5m': '5 minutes', '15m': '15 minutes', '30m': '30 minutes', '1h': 'hour', '4h': '4 hours', '1d': 'day', '1w': 'week', '1M': 'month' };

function renderOHLCV() {
    const data = symState.ohlcv;
    const c = $('ohlcv');
    if (!data || !data.length) { c.innerHTML = '<div class="no-data">No candle data returned.</div>'; return; }
    const rows = [...data].reverse();
    const cols = [
        { key: 'timestamp', label: 'When', left: true, fmt: n => isNum(n) ? fmtTs(n) : '—' },
        { key: 'open', label: 'Opened', fmt: n => price(n, priceCcy()) }, { key: 'high', label: 'Highest', fmt: n => price(n, priceCcy()) },
        { key: 'low', label: 'Lowest', fmt: n => price(n, priceCcy()) }, { key: 'close', label: 'Closed', fmt: n => price(n, priceCcy()) },
        { key: 'volume', label: 'Shares traded', fmt: n => volume(n, priceCcy()) },
    ];
    c.innerHTML = `<div class="info-banner">Each bar ("candle") shows one ${esc(TF_NAMES[symState.timeframe] || symState.timeframe)} of trading. <span class="pos">Green</span> means the price ended higher than it started, <span class="neg">red</span> means lower. The thin line shows the highest and lowest price. The small bars at the bottom show how much was traded.</div>
        <div class="chart-wrap"><canvas id="chart" aria-label="Candlestick chart"></canvas><canvas id="chartOverlay" aria-hidden="true"></canvas><div class="chart-tip" id="chartTip"></div></div>
        <div id="ohlcvTable"></div>`;
    renderTable('ohlcvTable', rows, cols);
    drawChart(true);
}

function cssVar(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }

const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
let chartAnim = 0;
let chartGeo = null;   // geometry shared by the base chart and the crosshair overlay

function setupCanvas(cv) {
    const dpr = window.devicePixelRatio || 1;
    const W = cv.clientWidth, H = cv.clientHeight;
    cv.width = W * dpr; cv.height = H * dpr;
    const g = cv.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { g, W, H };
}

/** Draws the chart. `progress` (0..1) reveals candles left to right for the entry animation. */
function drawChart(animate = false) {
    const cv = $('chart'); const data = symState.ohlcv;
    if (!cv || !data || !data.length) return;
    cancelAnimationFrame(chartAnim);
    const t0 = performance.now(), dur = animate && !reducedMotion() ? 650 : 0;
    const frame = now => {
        const p = dur ? Math.min(1, (now - t0) / dur) : 1;
        paintChart(cv, data, 1 - Math.pow(1 - p, 3));
        if (p < 1) chartAnim = requestAnimationFrame(frame);
    };
    chartAnim = requestAnimationFrame(frame);
}

function paintChart(cv, data, progress) {
    const { g, W, H } = setupCanvas(cv);
    g.clearRect(0, 0, W, H);
    const pos = cssVar('--pos'), neg = cssVar('--neg'), dim = cssVar('--text-dim'), grid = cssVar('--border'), cta = cssVar('--cta');
    const ccy = priceCcy();
    const padL = 10, padR = 70, padT = 18, padB = 24;
    const volH = (H - padT - padB) * 0.18;
    const plotH = H - padT - padB - volH - 8;
    const plotW = W - padL - padR;
    const hi = Math.max(...data.map(d => d.high)), lo = Math.min(...data.map(d => d.low));
    const span = (hi - lo) || 1;
    const vmax = Math.max(...data.map(d => d.volume || 0)) || 1;
    const step = plotW / data.length;
    const bw = Math.max(1, Math.min(14, step * 0.68));
    const y = p => padT + (hi - p) / span * plotH;
    const x = i => padL + step * i + step / 2;
    chartGeo = { padL, padR, padT, padB, W, H, step, hi, lo, span, plotH, y, x };

    g.font = '600 11px "Roboto Condensed", "Inter", sans-serif';
    g.textBaseline = 'middle';
    for (let i = 0; i <= 4; i++) {
        const v = lo + span * i / 4, yy = y(v);
        g.strokeStyle = grid; g.globalAlpha = .55; g.lineWidth = 1; g.setLineDash([2, 4]);
        g.beginPath(); g.moveTo(padL, yy); g.lineTo(W - padR, yy); g.stroke();
        g.setLineDash([]); g.globalAlpha = 1; g.fillStyle = dim; g.textAlign = 'left';
        g.fillText(price(v, ccy), W - padR + 8, yy);
    }

    const shown = Math.max(1, Math.ceil(data.length * progress));
    for (let i = 0; i < shown; i++) {
        const d = data[i];
        const up = d.close >= d.open, col = up ? pos : neg, xx = x(i);
        g.strokeStyle = col; g.fillStyle = col; g.lineWidth = 1;
        g.beginPath(); g.moveTo(xx, y(d.high)); g.lineTo(xx, y(d.low)); g.stroke();
        const top = y(Math.max(d.open, d.close)), h = Math.max(1, Math.abs(y(d.open) - y(d.close)));
        g.fillRect(xx - bw / 2, top, bw, h);
        const vh = (d.volume || 0) / vmax * volH;
        g.globalAlpha = .32; g.fillRect(xx - bw / 2, H - padB - vh, bw, vh); g.globalAlpha = 1;
    }

    // Last price line + label
    if (progress >= 1) {
        const last = data[data.length - 1], ly = y(last.close), up = last.close >= last.open;
        g.strokeStyle = cta; g.lineWidth = 1; g.setLineDash([5, 4]); g.globalAlpha = .9;
        g.beginPath(); g.moveTo(padL, ly); g.lineTo(W - padR, ly); g.stroke();
        g.setLineDash([]); g.globalAlpha = 1;
        g.fillStyle = cta; g.fillRect(W - padR + 2, ly - 9, padR - 4, 18);
        g.fillStyle = '#1c2833'; g.textAlign = 'center';
        g.fillText(price(last.close, ccy), W - padR / 2 + 1, ly + 0.5);
        void up;
    }

    g.fillStyle = dim; g.textBaseline = 'alphabetic';
    [0, Math.floor(data.length / 2), data.length - 1].forEach((i, n) => {
        g.textAlign = n === 0 ? 'left' : n === 2 ? 'right' : 'center';
        g.fillText(fmtTs(data[i].timestamp), n === 0 ? padL : n === 2 ? W - padR : x(i), H - 7);
    });

    const ov = $('chartOverlay');
    if (ov) setupCanvas(ov);   // clears the crosshair after a redraw
    cv.onmousemove = ev => crosshair(ev, data);
    cv.onmouseleave = () => { $('chartTip').textContent = ''; if (ov) setupCanvas(ov); };
    cv.style.pointerEvents = 'auto';
    $('chartOverlay').style.pointerEvents = 'none';
}

function crosshair(ev, data) {
    if (!chartGeo) return;
    const cv = $('chart'), r = cv.getBoundingClientRect();
    const { padL, padR, padT, W, H, padB, step, x, lo, span, plotH } = chartGeo;
    const i = Math.min(data.length - 1, Math.max(0, Math.floor((ev.clientX - r.left - padL) / step)));
    const d = data[i], ccy = priceCcy();
    $('chartTip').textContent = `${fmtTs(d.timestamp)}   O ${price(d.open, ccy)}   H ${price(d.high, ccy)}   L ${price(d.low, ccy)}   C ${price(d.close, ccy)}   Vol ${volume(d.volume, ccy)}`;
    const { g } = setupCanvas($('chartOverlay'));
    const yy = Math.min(padT + plotH, Math.max(padT, ev.clientY - r.top));
    g.strokeStyle = cssVar('--accent'); g.globalAlpha = .7; g.lineWidth = 1; g.setLineDash([3, 3]);
    g.beginPath(); g.moveTo(x(i), padT); g.lineTo(x(i), H - padB); g.moveTo(padL, yy); g.lineTo(W - padR, yy); g.stroke();
    g.setLineDash([]); g.globalAlpha = 1;
    const val = lo + span * (1 - (yy - padT) / plotH);
    g.fillStyle = cssVar('--accent'); g.fillRect(W - padR + 2, yy - 9, padR - 4, 18);
    g.fillStyle = '#06231d'; g.font = '700 11px "Roboto Condensed", "Inter", sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle';
    g.fillText(price(val, ccy), W - padR / 2 + 1, yy + 0.5);
}
let resizeTimer;
window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => drawChart(false), 120); });

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
function moversExplanation() {
    const market = GLOSS.catalog.markets.find(m => m.id === val('moversMarket'));
    const what = GLOSS.catalog.categories[val('moversCategory')];
    const bits = [];
    if (what) bits.push(what + '.');
    if (val('moversMarket').startsWith('stocks') || val('moversMarket') === 'crypto') bits.push('Only actively traded names on the main exchange are listed, so tiny or hard-to-trade ones do not crowd the list.');
    if (market && market.data) bits.push(market.data);
    return bits.join(' ');
}
function syncMoversHint() { $('moversHint').textContent = moversExplanation(); }
$('moversMarket').addEventListener('change', () => { syncCategoryOptions(); syncMoversHint(); });
$('moversCategory').addEventListener('change', syncMoversHint);
syncCategoryOptions();
glossLoaded.then(syncMoversHint);

function stopAuto() { clearInterval(autoTimer); autoTimer = null; }
$('moversAuto').addEventListener('change', e => {
    stopAuto();
    if (e.target.checked) { autoTimer = setInterval(() => fetchMovers(true), 60000); toast('This list will refresh every minute.', 'info'); }
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
        $('moversTitle').textContent = `${$('moversCategory').selectedOptions[0].textContent} · ${$('moversMarket').selectedOptions[0].textContent}`;
        $('moversExplain').textContent = moversExplanation();
        $('moversFilter').value = '';
        renderTable('moversContent', res.data, MOVER_COLS, { onRowClick: openSymbol });
        setDownloads('movers', '/api/download/movers', moversState);
        if (!quiet) toast(res.data.length ? `Here are ${res.data.length}. Tap one to see the full picture.` : 'Nothing to show right now.', res.data.length ? 'success' : 'info');
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
// Market cap is typed in the unit people use for that market: crore for India, millions elsewhere.
const CAP_UNITS = {
    india: { label: 'Company size at least (₹ crore)', mult: 1e7, ph: 'e.g. 5000', large: 50000 },
    america: { label: 'Company size at least ($ million)', mult: 1e6, ph: 'e.g. 2000', large: 10000 },
    uk: { label: 'Company size at least (£ million)', mult: 1e6, ph: 'e.g. 2000', large: 10000 },
    canada: { label: 'Company size at least (C$ million)', mult: 1e6, ph: 'e.g. 2000', large: 10000 },
    germany: { label: 'Company size at least (€ million)', mult: 1e6, ph: 'e.g. 2000', large: 10000 },
};
const capUnit = () => CAP_UNITS[val('screenerMarket')] || { label: 'Company size at least (in money units)', mult: 1, ph: 'e.g. 1000000000', large: 1e10 };
function syncCapUnit() {
    const u = capUnit();
    $('screenerCapLabel').textContent = u.label;
    $('screenerMinCap').placeholder = u.ph;
}
$('screenerMarket').addEventListener('change', syncCapUnit);
syncCapUnit();

const PRESETS = [
    { name: 'Rising today, actively traded', about: 'Up at least 2% with plenty of buying and selling.', set: () => ({ screenerMinChange: 2, screenerMinVol: 500000, screenerSort: 'change', screenerOrder: 'desc' }) },
    { name: 'Falling today, actively traded', about: 'Down 2% or more with plenty of buying and selling.', set: () => ({ screenerMaxChange: -2, screenerMinVol: 500000, screenerSort: 'change', screenerOrder: 'asc' }) },
    { name: 'Most talked-about', about: 'The stocks that changed hands the most today.', set: () => ({ screenerSort: 'volume', screenerOrder: 'desc' }) },
    { name: 'Biggest companies', about: 'The largest companies by total value.', set: () => ({ screenerMinCap: capUnit().large, screenerSort: 'market_cap_basic', screenerOrder: 'desc' }) },
    { name: 'Low-priced and busy', about: 'Priced under 100 per share and very actively traded.', set: () => ({ screenerMaxPrice: 100, screenerMinVol: 1000000, screenerSort: 'volume', screenerOrder: 'desc' }) },
];
$('screenerPresets').innerHTML = PRESETS.map((p, i) =>
    `<button type="button" class="preset-card" data-i="${i}"><strong>${esc(p.name)}</strong><span>${esc(p.about)}</span></button>`).join('');
$('screenerPresets').addEventListener('click', e => {
    const b = e.target.closest('.preset-card'); if (!b) return;
    resetScreener(false);
    Object.entries(PRESETS[+b.dataset.i].set()).forEach(([id, v]) => { $(id).value = v; });
    pendingPreset = PRESETS[+b.dataset.i].name;
    $('screenerForm').requestSubmit();
});
let screenerLabel = '', pendingPreset = '';
function resetScreener(clearResults = true) {
    Object.keys(SCREENER_FIELDS).forEach(id => { $(id).value = ''; });
    if (clearResults) screenerLabel = '';
    $('screenerSort').value = 'volume'; $('screenerOrder').value = 'desc'; $('screenerLimit').value = 25;
    if (clearResults) $('screenerResults').hidden = true;
}
$('screenerReset').addEventListener('click', () => { screenerLabel = ''; resetScreener(); });

$('screenerForm').addEventListener('submit', async e => {
    e.preventDefault();
    screenerLabel = pendingPreset;
    pendingPreset = '';
    const params = {
        market: val('screenerMarket'), sort_by: val('screenerSort'), sort_order: val('screenerOrder'),
        limit: Math.min(200, Math.max(1, parseInt(val('screenerLimit'), 10) || 25)),
    };
    params.main_only = $('screenerMain').checked;
    Object.entries(SCREENER_FIELDS).forEach(([id, key]) => {
        if (val(id) === '') return;
        params[key] = id === 'screenerMinCap' ? Number(val(id)) * capUnit().mult : val(id);
    });
    setLoading('screenerBtn', true);
    try {
        const res = await api(`/api/screener?${qs(params)}`);
        $('screenerResults').hidden = false;
        const total = res.totalCount ?? res.total;
        const found = total ? Number(total).toLocaleString('en-US') : res.data.length;
        $('screenerTitle').textContent = `${screenerLabel || 'Matching stocks'} · ${$('screenerMarket').selectedOptions[0].textContent}`;
        $('screenerExplain').textContent = res.data.length
            ? `${found} stocks match your search. Showing the first ${res.data.length}. Tap a row to see the full picture.`
            : 'No stocks match. Try loosening one of the rules.';
        $('screenerFilter').value = '';
        renderTable('screenerContent', res.data, SCREENER_COLS, { onRowClick: openSymbol });
        setDownloads('screener', '/api/download/screener', params);
        toast(res.data.length ? `Found ${found} matching stocks.` : 'Nothing matches those rules.', res.data.length ? 'success' : 'info');
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
