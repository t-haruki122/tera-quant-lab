const periods = { '1M': '1mo', '3M': '3mo', '6M': '6mo', '1Y': '1y', '2Y': '2y', '10Y': '10y' };
export const DEFAULT_PERIOD = '3mo';

export function normalizeSymbol(value) {
    const symbol = typeof value === 'string' ? value.trim() : '';
    return /^[A-Za-z0-9^][A-Za-z0-9.^=\-]{0,19}$/.test(symbol) ? symbol.toUpperCase() : null;
}

function runId(value) {
    const text = String(value ?? '');
    const id = Number(text);
    return /^[1-9][0-9]*$/.test(text) && Number.isSafeInteger(id) ? id : null;
}

export function parseRoute(hash) {
    const [path, query = ''] = hash.split(/\?(.*)/s, 2);
    const params = new URLSearchParams(query);
    if (path === '#/stats' && !query) return { view: 'stats' };
    if (path === '#/portfolio' && !query) return { view: 'portfolio' };
    if (path === '#/backtest') {
        if (params.has('run')) {
            const run = runId(params.get('run'));
            return run ? { view: 'backtest', run } : { view: 'list' };
        }
        const raw = params.get('symbol');
        const symbol = normalizeSymbol(raw);
        if (raw === null) return { view: 'backtest' };
        if (symbol) return { view: 'backtest', symbol };
    }
    const match = /^#\/stock\/([^/]+)$/.exec(path);
    if (match) {
        try {
            const symbol = normalizeSymbol(decodeURIComponent(match[1]));
            const raw = params.get('period');
            const period = raw === null ? DEFAULT_PERIOD : periods[raw.toUpperCase()];
            if (symbol && period) return { view: 'detail', symbol, period };
        } catch {
            // 不正なパーセントエンコードはダッシュボードへ戻す。
        }
    }
    return { view: 'list' };
}

export function serializeRoute({ view, symbol, period, run }) {
    symbol = normalizeSymbol(symbol);
    if (view === 'stats') return '#/stats';
    if (view === 'portfolio') return '#/portfolio';
    if (view === 'backtest') {
        const id = runId(run);
        if (id) return `#/backtest?run=${id}`;
        return symbol ? `#/backtest?symbol=${encodeURIComponent(symbol)}` : '#/backtest';
    }
    if (view === 'detail' && symbol) {
        const code = Object.keys(periods).find(key => periods[key] === period);
        const query = code && period !== DEFAULT_PERIOD ? `?period=${code}` : '';
        return `#/stock/${encodeURIComponent(symbol)}${query}`;
    }
    return '#/';
}
