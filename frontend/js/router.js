const periods = { '1M': '1mo', '3M': '3mo', '6M': '6mo', '1Y': '1y', '2Y': '2y', '10Y': '10y' };
export const DEFAULT_PERIOD = '3mo';

export const isFundSymbol = symbol => /^FUND-[A-Z0-9]{8}$/.test(symbol);

export function normalizeSymbol(value) {
    const valueTrimmed = typeof value === 'string' ? value.trim() : '';
    if (!/^[A-Za-z0-9^][A-Za-z0-9.^=\-]{0,19}$/.test(valueTrimmed)) return null;
    const symbol = valueTrimmed.toUpperCase();
    return symbol.startsWith('FUND-') && !isFundSymbol(symbol) ? null : symbol;
}

export function normalizeLookupSymbol(value) {
    const code = value.normalize('NFKC').trim().toUpperCase();
    return /^[0-9][A-Z0-9]{7}$/.test(code) ? `FUND-${code}` : normalizeSymbol(value);
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
    if (path === '#/market' && !query) return { view: 'market' };
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
    if (view === 'market') return '#/market';
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
