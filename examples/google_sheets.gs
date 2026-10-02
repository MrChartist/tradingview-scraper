/**
 * Tickvale in Google Sheets.
 *
 * Setup (once):
 *   Extensions > Apps Script > paste this file > Project Settings > Script properties:
 *     TICKVALE_URL = https://your-tickvale-host
 *     TICKVALE_KEY = the key for this sheet (give it its own key with only the 'quotes' operation)
 *
 * Use in a cell:
 *   =TICKVALE("reliance")                 the price
 *   =TICKVALE("reliance", "change_percent")
 *   =TICKVALE("NSE:TCS", "freshness")
 *   =TICKVALE(A2:A10)                      a column of names, one price each
 *
 * Fields you can ask for: price, change, change_percent, open, high, low, volume, currency, freshness, name.
 * Results are cached for 60 seconds, so a big sheet does not hammer the server.
 *
 * NOTE: written for Apps Script; it has not been run from this repository's tests.
 */
function TICKVALE(symbols, field) {
  field = field || 'price';
  const list = Array.isArray(symbols) ? symbols.flat().filter(String) : [String(symbols)];
  if (!list.length) return '';
  const props = PropertiesService.getScriptProperties();
  const base = (props.getProperty('TICKVALE_URL') || '').replace(/\/+$/, '');
  const key = props.getProperty('TICKVALE_KEY');
  if (!base) return 'Set TICKVALE_URL in Script properties';

  const cache = CacheService.getScriptCache();
  const out = {};
  const missing = [];
  list.forEach(s => {
    const hit = cache.get('tv:' + s.toLowerCase());
    if (hit) out[s] = JSON.parse(hit); else missing.push(s);
  });

  if (missing.length) {
    // Plain names are fine: resolve them first, then ask for all prices in one request.
    const headers = key ? { 'X-API-Key': key } : {};
    const names = {};
    missing.forEach(s => {
      if (s.indexOf(':') > 0) { names[s] = s.toUpperCase(); return; }
      const r = JSON.parse(UrlFetchApp.fetch(base + '/v1/symbols/resolve?q=' + encodeURIComponent(s), { headers: headers, muteHttpExceptions: true }).getContentText());
      names[s] = r.data && r.data.best ? r.data.best.full_symbol : null;
    });
    const codes = Object.values(names).filter(Boolean);
    if (codes.length) {
      const res = JSON.parse(UrlFetchApp.fetch(base + '/v1/quotes?symbols=' + encodeURIComponent(codes.join(',')), { headers: headers, muteHttpExceptions: true }).getContentText());
      if (res.error) return res.error.message;
      const bySymbol = {};
      res.data.forEach(q => { bySymbol[q.symbol] = q; });
      missing.forEach(s => {
        const q = names[s] && bySymbol[names[s]];
        if (q) { out[s] = q; cache.put('tv:' + s.toLowerCase(), JSON.stringify(q), 60); }
      });
    }
  }
  const values = list.map(s => (out[s] && out[s][field] !== undefined && out[s][field] !== null) ? out[s][field] : 'n/a');
  return Array.isArray(symbols) ? values.map(v => [v]) : values[0];
}
