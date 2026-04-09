// ═══════════════════════════════════════════════════════════════════
//  STATE
// ═══════════════════════════════════════════════════════════════════
let sym = { exchange: '', ticker: '', timeframe: '1d', candles: 100, activeTab: 'overview' };
let symData = { overview: null, indicators: null, fundamentals: null, ohlcv: null };
let moversState = { market: 'stocks-usa', category: 'gainers', limit: 25 };
let screenerState = { market: 'america' };

// ═══════════════════════════════════════════════════════════════════
//  MODE NAVIGATION
// ═══════════════════════════════════════════════════════════════════
document.querySelectorAll('.mode-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.mode-section').forEach(s => s.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById('mode-' + btn.dataset.mode).classList.add('active');
    });
});

// ═══════════════════════════════════════════════════════════════════
//  SYMBOL LOOKUP
// ═══════════════════════════════════════════════════════════════════
document.getElementById('searchForm').addEventListener('submit', async e => {
    e.preventDefault();
    sym.exchange  = val('exchange').toUpperCase();
    sym.ticker    = val('ticker').toUpperCase();
    sym.timeframe = val('timeframe');
    sym.candles   = parseInt(val('candles')) || 100;
    if (!sym.exchange || !sym.ticker) return;

    setLoading('searchBtn', 'spinner', true);
    try {
        const [o, i, f] = await Promise.allSettled([
            safeFetch(`/api/overview/${sym.exchange}/${sym.ticker}`),
            safeFetch(`/api/indicators/${sym.exchange}/${sym.ticker}`),
            safeFetch(`/api/fundamentals/${sym.exchange}/${sym.ticker}`)
        ]);
        symData.overview     = ex(o);
        symData.indicators   = ex(i);
        symData.fundamentals = ex(f);
        symData.ohlcv        = null;
        if (!symData.overview && !symData.indicators && !symData.fundamentals) {
            toast("Failed to fetch data. Verify exchange/ticker.", "error");
        } else {
            toast(`${sym.exchange}:${sym.ticker} scraped!`, "success");
            show('symbolTerminal');
            renderSymbol();
        }
    } catch { toast("Network error.", "error"); }
    finally { setLoading('searchBtn', 'spinner', false); }
});

// Tab switching
document.querySelectorAll('#symbolTabs .tab-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
        document.querySelectorAll('#symbolTabs .tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('#content-container .content-pane').forEach(c => c.classList.remove('active'));
        btn.classList.add('active');
        sym.activeTab = btn.dataset.tab;
        document.getElementById(sym.activeTab).classList.add('active');
        if (sym.activeTab === 'ohlcv' && !symData.ohlcv) await loadOHLCV();
    });
});

function renderSymbol() {
    renderGrid('overview', symData.overview);
    renderGrid('indicators', symData.indicators);
    renderGrid('fundamentals', symData.fundamentals);
    if (symData.ohlcv) renderOHLCV(symData.ohlcv);
    else el('ohlcv').innerHTML = '<div class="no-data">Click this tab to load historical OHLCV pricing.</div>';
}

async function loadOHLCV() {
    el('ohlcv').innerHTML = '<div class="info-banner">⏳ Connecting to TradingView WebSocket… may take 10-20s.</div>';
    const res = await safeFetch(`/api/ohlcv/${sym.exchange}/${sym.ticker}?timeframe=${sym.timeframe}&candles=${sym.candles}`);
    if (res && res.data) { symData.ohlcv = res.data; renderOHLCV(res.data); toast(`${res.data.length} candles loaded!`, "success"); }
    else el('ohlcv').innerHTML = '<div class="no-data">Could not fetch OHLCV data.</div>';
}

function triggerDownload(fmt) {
    if (!sym.exchange || !sym.ticker) return;
    const tab = sym.activeTab;
    const url = tab === 'ohlcv'
        ? `/api/download/ohlcv/${sym.exchange}/${sym.ticker}?timeframe=${sym.timeframe}&candles=${sym.candles}&fmt=${fmt}`
        : `/api/download/${tab}/${sym.exchange}/${sym.ticker}?fmt=${fmt}`;
    window.open(url, '_blank');
}

// ═══════════════════════════════════════════════════════════════════
//  MARKET MOVERS
// ═══════════════════════════════════════════════════════════════════
document.getElementById('moversForm').addEventListener('submit', async e => {
    e.preventDefault();
    moversState.market   = val('moversMarket');
    moversState.category = val('moversCategory');
    moversState.limit    = parseInt(val('moversLimit')) || 25;
    setLoading('moversBtn', 'moversSpinner', true);
    try {
        const res = await safeFetch(`/api/movers?market=${moversState.market}&category=${moversState.category}&limit=${moversState.limit}`);
        if (res && res.data) {
            show('moversTerminal');
            el('moversTitle').textContent = `${moversState.category.replace(/-/g,' ')} — ${moversState.market} (${res.data.length} results)`;
            renderTable('moversContent', res.data);
            toast(`${res.data.length} movers loaded!`, "success");
        } else toast("No movers data found.", "error");
    } catch { toast("Network error.", "error"); }
    finally { setLoading('moversBtn', 'moversSpinner', false); }
});

function downloadMovers(fmt) {
    window.open(`/api/download/movers?market=${moversState.market}&category=${moversState.category}&limit=${moversState.limit}&fmt=${fmt}`, '_blank');
}

// ═══════════════════════════════════════════════════════════════════
//  SCREENER
// ═══════════════════════════════════════════════════════════════════
document.getElementById('screenerForm').addEventListener('submit', async e => {
    e.preventDefault();
    screenerState.market = val('screenerMarket');
    const limit = parseInt(val('screenerLimit')) || 25;
    let qs = `market=${screenerState.market}&limit=${limit}`;
    const minP = val('screenerMinPrice'); if (minP) qs += `&min_price=${minP}`;
    const maxP = val('screenerMaxPrice'); if (maxP) qs += `&max_price=${maxP}`;
    const minV = val('screenerMinVol');   if (minV) qs += `&min_volume=${minV}`;
    const minC = val('screenerMinCap');   if (minC) qs += `&min_market_cap=${minC}`;
    screenerState._qs = qs; // save for downloads

    setLoading('screenerBtn', 'screenerSpinner', true);
    try {
        const res = await safeFetch(`/api/screener?${qs}`);
        if (res && res.data) {
            show('screenerTerminal');
            el('screenerTitle').textContent = `${screenerState.market} — ${res.data.length} of ${res.totalCount || '?'} matches`;
            renderTable('screenerContent', res.data);
            toast(`${res.data.length} results!`, "success");
        } else toast("No screener results.", "error");
    } catch { toast("Network error.", "error"); }
    finally { setLoading('screenerBtn', 'screenerSpinner', false); }
});

function downloadScreener(fmt) {
    window.open(`/api/download/screener?${screenerState._qs || 'market=america'}&fmt=${fmt}`, '_blank');
}

// ═══════════════════════════════════════════════════════════════════
//  RENDERERS
// ═══════════════════════════════════════════════════════════════════
function renderGrid(id, data) {
    const c = el(id);
    if (!data) { c.innerHTML = '<div class="no-data">No data available.</div>'; return; }
    let h = '<div class="data-grid">';
    for (const [k, v] of Object.entries(data)) {
        if (v === null || typeof v === 'object') continue;
        let dv = v, cls = '';
        if (typeof v === 'number') {
            dv = fmtNum(v);
            if (k.toLowerCase().includes('change') || k.toLowerCase().includes('perf')) {
                cls = v > 0 ? 'val-pos' : v < 0 ? 'val-neg' : '';
                if (v > 0) dv = '+' + dv;
            }
        }
        h += `<div class="data-card"><div class="data-label">${k.replace(/_/g,' ').replace(/\./g,' ')}</div><div class="data-value ${cls}">${dv}</div></div>`;
    }
    c.innerHTML = h + '</div>';
}

function renderOHLCV(data) {
    const c = el('ohlcv');
    if (!data || !data.length) { c.innerHTML = '<div class="no-data">No OHLCV data.</div>'; return; }
    let h = `<div class="info-banner">${data.length} candles · ${sym.exchange}:${sym.ticker} · ${sym.timeframe}</div>`;
    h += '<div class="table-wrap"><table><thead><tr><th>#</th><th>Date</th><th>Open</th><th>High</th><th>Low</th><th>Close</th><th>Volume</th></tr></thead><tbody>';
    for (const r of data) {
        const chg = r.close - r.open;
        const cls = chg >= 0 ? 'pos' : 'neg';
        const ts = r.timestamp ? new Date(r.timestamp * 1000).toLocaleDateString('en-IN', { day:'2-digit', month:'short', year:'numeric' }) : '—';
        h += `<tr><td>${r.index??''}</td><td>${ts}</td><td>${f2(r.open)}</td><td>${f2(r.high)}</td><td>${f2(r.low)}</td><td class="${cls}">${f2(r.close)}</td><td>${r.volume != null ? fmtNum(r.volume) : '—'}</td></tr>`;
    }
    c.innerHTML = h + '</tbody></table></div>';
}

function renderTable(containerId, data) {
    const c = el(containerId);
    if (!data || !data.length) { c.innerHTML = '<div class="no-data">No data.</div>'; return; }
    const keys = Object.keys(data[0]);
    let h = '<div class="table-wrap"><table><thead><tr>';
    keys.forEach(k => h += `<th>${k.replace(/_/g,' ')}</th>`);
    h += '</tr></thead><tbody>';
    for (const row of data) {
        h += '<tr>';
        for (const k of keys) {
            let v = row[k];
            let cls = '';
            if (typeof v === 'number') {
                if (k === 'change' || k === 'change_abs') { cls = v > 0 ? 'pos' : v < 0 ? 'neg' : ''; }
                v = fmtNum(v);
                if ((k === 'change' || k === 'change_abs') && parseFloat(v) > 0) v = '+' + v;
            }
            if (v === null || v === undefined) v = '—';
            h += `<td class="${cls}">${v}</td>`;
        }
        h += '</tr>';
    }
    c.innerHTML = h + '</tbody></table></div>';
}

// ═══════════════════════════════════════════════════════════════════
//  UTILITIES
// ═══════════════════════════════════════════════════════════════════
function el(id) { return document.getElementById(id); }
function val(id) { return el(id).value.trim(); }
function show(id) { el(id).style.display = 'flex'; }

async function safeFetch(url) {
    try { const r = await fetch(url); return r.ok ? await r.json() : null; }
    catch { return null; }
}

function ex(s) { return s.status === 'fulfilled' && s.value ? s.value.data : null; }

function fmtNum(n) {
    if (n == null) return '—';
    const a = Math.abs(n);
    if (a >= 1e12) return (n / 1e12).toFixed(2) + 'T';
    if (a >= 1e9) return (n / 1e9).toFixed(2) + 'B';
    if (a >= 1e6) return (n / 1e6).toFixed(2) + 'M';
    if (a >= 1e3 && n % 1 !== 0) return n.toFixed(2);
    if (n % 1 !== 0) return n.toFixed(4);
    return n.toLocaleString();
}
function f2(v) { return v != null ? Number(v).toFixed(2) : '—'; }

function setLoading(btnId, spinnerId, on) {
    const btn = el(btnId); const sp = el(spinnerId);
    btn.disabled = on;
    btn.querySelector('.btn-text').style.display = on ? 'none' : '';
    sp.style.display = on ? 'block' : 'none';
}

let _tt;
function toast(msg, type) {
    const t = el('toast'); t.textContent = msg; t.className = `toast show ${type}`;
    clearTimeout(_tt); _tt = setTimeout(() => t.className = 'toast', 4000);
}
