import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { elements, fromRepo } from './helpers/dom-shim.mjs';

const { state, cache } = await fromRepo('frontend/js/state.js');
const { fetchAPI } = await fromRepo('frontend/js/api.js');
const { bindBacktestEvents, openBacktest, renderStrategies, renderRunResult, renderGridResult, tradesToCsv } =
    await fromRepo('frontend/js/backtest.js');
const { escapeHtml } = await fromRepo('frontend/js/utils.js');
const el = name => document.getElementById(`backtest-${name}`);
const response = (data, status = 200) => ({ ok: status < 400, status, json: async () => data });
const payload = '"><img src=x onerror="alert(1)"><svg/onload=alert(1)>';
const registry = {
    strategies: [
        { name: payload, params: [
            { name: payload, description: payload, type: 'int', min: 1, max: 7, default: 3 },
            { name: 'exit', description: 'Exit', type: 'str', choices: [payload, 'upper'], default: payload },
        ] },
        { name: 'momentum', params: [
            { name: 'period', description: 'バー数', type: 'int', default: 20 },
        ] },
    ],
    rank_metrics: ['sharpe', 'return_pct'],
};
const result = {
    metrics: { return_pct: 12.34, sharpe: 1.1, sortino: null, total_trades: 1 },
    benchmark_metrics: { return_pct: 2.34, sharpe: 0.5, total_trades: 0 },
    equity_curve: [{ date: '2024-02-03', equity: 120, benchmark_equity: 102 }],
    trades: [{ symbol: payload, date: '2024-02-03', side: 'BUY' }], warnings: [payload],
};

function assertEscaped(html) {
    assert.ok(html.includes(escapeHtml(payload)));
    assert.doesNotMatch(html, /<img|<svg|" onerror=/i);
}

test('signed-out backtest opens the login modal and does not request data', () => {
    state.currentUser = null;
    state.currentSymbol = '7203.T';
    globalThis.fetch = () => assert.fail('signed-out backtest requested data');
    bindBacktestEvents();
    openBacktest();
    assert.equal(el('login').classList.contains('hidden'), false);
    assert.equal(el('form').classList.contains('hidden'), true);
    assert.match(readFileSync('frontend/index.html', 'utf8'), /バックテストを実行するにはログインが必要です。/);
    assert.equal(el('symbol').value, '7203.T');
    el('login-btn').click();
    assert.equal(document.getElementById('auth-modal-overlay').classList.contains('hidden'), false);
});

test('registry, parameter controls, and watchlist escape text and attributes', async () => {
    state.currentUser = { id: 1, access_token: 'test' };
    state.listItems = [{ symbol: payload }, { symbol: '^N225' }];
    const urls = [];
    globalThis.fetch = async (url, options) => {
        urls.push(url);
        assert.equal(options.headers.get('Authorization'), 'Bearer test');
        return response(registry);
    };
    await openBacktest('BRK.B');
    assert.equal(el('symbol').value, 'BRK.B');
    assert.deepEqual(urls, ['/backtest/strategies']);
    assert.equal(el('login').classList.contains('hidden'), true);
    assert.equal(el('form').classList.contains('hidden'), false);
    assertEscaped(el('watchlist-symbol').innerHTML);
    assertEscaped(el('strategy').innerHTML);
    assertEscaped(el('params').innerHTML);
    assertEscaped(el('grid-params').innerHTML);
    assert.match(el('params').innerHTML, /type="number" value="3" min="1" max="7" step="1"/);
    assert.match(el('params').innerHTML, /<select id="backtest-param-1">/);
    assert.match(el('rank-by').innerHTML, /value="sharpe" selected/);
    el('strategy').value = 'momentum';
    el('strategy').dispatchEvent(new Event('change'));
    assert.match(el('params').innerHTML, /value="20"/);
});

test('renders metrics, trades, warnings, grid, and replaces the equity chart safely', () => {
    const charts = [];
    globalThis.Chart = class {
        constructor(canvas, config) { this.canvas = canvas; this.config = config; charts.push(this); }
        destroy() { this.destroyed = true; }
    };
    renderRunResult(result, 'BRK.B');
    assert.equal(el('result-title').textContent, 'BRK.B のバックテスト結果');
    assert.equal((el('metrics').innerHTML.match(/class="metric-card"/g) ?? []).length, 8);
    assert.match(el('metrics').innerHTML, /12\.34%/);
    assert.match(el('metrics').innerHTML, /買い持ち 2\.34%/);
    assertEscaped(el('trades').innerHTML);
    assertEscaped(el('warnings').innerHTML);
    renderRunResult(result, 'BRK.B');
    assert.equal(charts[0].destroyed, true);
    assert.deepEqual(charts[1].config.data.datasets.map(series => series.data), [[120], [102]]);
    renderGridResult({ rank_by: payload, results: [{ rank: 1, params: { [payload]: payload }, metrics: {} }] });
    assertEscaped(el('grid-table').innerHTML);
    assert.match(el('grid-table').innerHTML, /data-backtest-row="0">この設定で実行/);
});

test('CSV quotes commas, newlines and quotes and neutralises every formula prefix', () => {
    const text = tradesToCsv([
        { symbol: 'A,"B\nC"', date: '=1+1', signal_date: '+1', side: '-1', price: '@1', pnl: -12 },
        { symbol: ' \t=1', date: 'plain' },
    ]);
    assert.ok(text.startsWith('"symbol","date","signal_date"'));
    assert.ok(text.includes('"A,""B\nC"""'));
    for (const value of ['=1+1', '+1', '-1', '@1', '-12', ' \t=1']) assert.ok(text.includes(`"\t${value}"`));
    assert.ok(text.includes('"plain"'));
    assert.ok(text.endsWith('\r\n'));
});

test('grid mode posts typed candidates and a grid row reruns its original base options', async () => {
    renderStrategies(registry);
    for (const [name, value] of Object.entries({
        symbol: 'BRK.B', start: '2024-01-01', end: '2024-12-31', interval: '1d', strategy: 'momentum',
        execution: 'next_open', fee: '0.001', slippage: '5', capital: '10000', mode: 'grid', 'rank-by': 'sharpe',
    })) el(name).value = value;
    el('strategy').dispatchEvent(new Event('change'));
    el('mode').dispatchEvent(new Event('change'));
    el('param-0').value = '20';
    el('grid-param-0').value = '5, 10';
    const requests = [];
    let returnGrid;
    globalThis.fetch = async (path, options) => {
        requests.push({ path, body: JSON.parse(options.body) });
        if (path === '/backtest/grid') return new Promise(resolve => { returnGrid = resolve; });
        return response(result);
    };
    el('form').dispatchEvent(new Event('submit'));
    assert.equal(el('fields').disabled, true);
    assert.match(el('status').textContent, /実行中/);
    assert.equal(requests[0].path, '/backtest/grid');
    assert.deepEqual(requests[0].body.grid, { period: [5, 10] });
    returnGrid(response({ rank_by: 'sharpe', results: [{ rank: 1, params: { period: 5 }, metrics: {} }] }));
    await new Promise(setImmediate);
    assert.equal(el('fields').disabled, false);
    el('symbol').value = 'CHANGED';
    el('grid-table').dispatchEvent({
        type: 'click', target: { closest: () => ({ dataset: { backtestRow: '0' } }) },
    });
    await new Promise(setImmediate);
    assert.equal(requests[1].path, '/backtest/run');
    assert.equal(requests[1].body.symbol, 'BRK.B');
    assert.deepEqual(requests[1].body.params, { period: 5 });
    assert.equal(requests[1].body.grid, undefined);
    assert.match(el('result-title').textContent, /BRK\.B/);

    globalThis.fetch = async () => response({ detail: payload }, 504);
    el('form').dispatchEvent(new Event('submit'));
    await new Promise(setImmediate);
    assert.equal(el('error').textContent, payload);
    assert.equal(el('error').classList.contains('hidden'), false);
});

test('fetchAPI never triggers stats and keeps encoded path segments intact', async () => {
    cache.clear();
    const calls = [];
    globalThis.fetch = async path => {
        calls.push(path);
        return response({});
    };
    const encodedPath = `/stock/${encodeURIComponent('A/B?# %')}/history`;
    await fetchAPI(encodedPath);
    await fetchAPI('/backtest/run', { method: 'POST' });
    assert.deepEqual(calls, [encodedPath, '/backtest/run']);
});

test('backtest displays Japanese HTTP errors and joins usable validation messages', async () => {
    elements.clear();
    state.currentUser = { id: 1, access_token: 'test' };
    bindBacktestEvents();
    renderStrategies(registry);
    el('strategy').value = 'momentum';
    el('param-0').value = '20';
    el('symbol').value = '7203.T';

    const cases = [
        [{ ok: false, status: 500, json: async () => { throw new SyntaxError('plain text body'); } },
            'バックテストの実行に失敗しました（HTTP 500）'],
        [{ ...response({ detail: 'サーバー内部でエラーが発生しました' }, 500),
            headers: new Headers({ 'X-Request-ID': 'test-123' }) },
        'サーバー内部でエラーが発生しました（リクエストID: test-123）'],
        [response({ detail: [{ msg: '開始日が不正です' }, null, { msg: '銘柄が不正です' }, { msg: {} }] }, 422),
            '開始日が不正です, 銘柄が不正です'],
        [response({ detail: [{ msg: '   ' }, null, {}] }, 422), 'バックテストの実行に失敗しました（HTTP 422）'],
    ];
    for (const [reply, expected] of cases) {
        globalThis.fetch = async path => {
            assert.equal(path, '/backtest/run');
            return reply;
        };
        el('form').dispatchEvent(new Event('submit'));
        await new Promise(setImmediate);
        assert.equal(el('error').textContent, expected);
        assert.equal(el('error').classList.contains('hidden'), false);
    }
});

test('shared fetchAPI uses a Japanese fallback for unusable error details', async () => {
    cache.clear();
    for (const body of [null, {}, { detail: '   ' }, { detail: {} }, { detail: [{ msg: 1 }] }]) {
        globalThis.fetch = async () => response(body, 503);
        await assert.rejects(fetchAPI('/stock/7203.T/history'), { message: 'リクエストに失敗しました（HTTP 503）' });
    }
});
