import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { assertAccessibleControls } from './helpers/a11y.mjs';
import { elements, fromRepo } from './helpers/dom-shim.mjs';

const { state } = await fromRepo('frontend/js/state.js');
const { bindBacktestEvents, openBacktest, readBacktestRequest, renderAdvancedResult, renderStrategies, tradesToCsv } =
    await fromRepo('frontend/js/backtest.js');
const { comparisonTable, normalizedComparison, renderSavedRuns } = await fromRepo('frontend/js/backtest-saved.js');
const { escapeHtml } = await fromRepo('frontend/js/utils.js');
const el = name => document.getElementById(`backtest-${name}`);
const tick = () => new Promise(setImmediate);
const reply = (data, status = 200) => ({ ok: status < 400, status, json: async () => data });
const attack = '<img src=x onerror="alert(1)">';
const data = {
    metrics: { return_pct: 10 }, benchmark_metrics: { return_pct: 7 }, trades: [], warnings: [],
    equity_curve: [{ date: '2024-01-01', equity: 100, benchmark_equity: 100 },
        { date: '2024-01-03', equity: 110, benchmark_equity: 107, holdings: { [attack]: 20 } }],
};
const row = {
    id: 7, kind: 'single', name: attack, symbol: 'AAA', start_date: '2024-01-01', end_date: '2024-01-03',
    created_at: '2024-01-04T10:00:00Z', note: 'メモ', summary: { return_pct: 10, max_drawdown: 1, sharpe: 2 },
    request: { symbol: 'AAA' }, result: data,
};
const registry = {
    strategies: [{ name: 'momentum', params: [{ name: 'period', default: 1, type: 'int' }] }], rank_metrics: ['sharpe'],
};
const assertEscaped = html => {
    assert.ok(html.includes(escapeHtml(attack)));
    assert.doesNotMatch(html, /<img\b/);
};

test('mode switching validates watchlist inputs and sends both advanced endpoints', async () => {
    elements.clear();
    state.currentUser = { id: 1, access_token: 'advanced' };
    renderStrategies(registry);
    bindBacktestEvents();
    el('strategy').value = 'momentum';
    el('param-0').value = '1';
    el('kind').value = 'portfolio';
    el('kind').dispatchEvent(new Event('change'));
    assert.equal(el('symbol').disabled, true);
    assert.equal(el('portfolio-options').classList.contains('hidden'), false);
    assert.equal(el('grid-options').classList.contains('hidden'), true);
    el('portfolio-symbols').value = 'aaa';
    assert.throws(readBacktestRequest, /2〜10件/);
    el('watchlist-symbol').value = '^n225';
    el('add-symbol').click();
    assert.equal(el('portfolio-symbols').value, 'aaa, ^N225');
    el('allocation').value = 'equal_active';
    el('rebalance').value = '21';
    const requests = [];
    const fold = { fold: 1, train_start: attack, train_end: '2024-01-01', test_start: '2024-01-02', test_end: '2024-01-03',
        params: { [attack]: attack }, is_metric: null, oos_metrics: { sharpe: 1 },
        warning: `有効な指標が得られなかったため、先頭のパラメータを使用しました。 ${attack}` };
    const trades = [{ symbol: 'AAA', side: 'BUY', reason: 'signal' }, { symbol: 'AAA', side: 'SELL', reason: 'rebalance' }];
    globalThis.Chart = class { destroy() {} };
    globalThis.fetch = async (url, options) => {
        requests.push([url, JSON.parse(options.body)]);
        return url.endsWith('/portfolio') ? reply({ ...data, trades, contributions: { [attack]: 85789.58 } })
            : reply({ ...data, folds: [fold], robustness: { rank_by: 'sharpe', profitable_share: 1,
                metric_degradation: 1, paired_folds: 1 } });
    };
    el('form').dispatchEvent(new Event('submit'));
    await tick();
    assert.equal(requests[0][0], '/backtest/portfolio');
    assert.deepEqual(requests[0][1].symbols, ['AAA', '^N225']);
    assert.equal(requests[0][1].rebalance_every, 21);
    assert.equal(requests[0][1].rebalance_threshold_pct, 5);
    assertEscaped(el('advanced-table').innerHTML);
    assert.match(el('advanced-table').innerHTML, /最終保有額/);
    assert.match(el('advanced-table').innerHTML, /<td>85,790<\/td>/);
    assert.match(el('trades').innerHTML, /<th>理由<\/th>/);
    assert.match(el('trades').innerHTML, /<td>シグナル<\/td>/);
    assert.match(el('trades').innerHTML, /<td>リバランス<\/td>/);
    assert.match(tradesToCsv(trades), /"理由"\r\n.*"シグナル"\r\n.*"リバランス"\r\n/);
    el('kind').value = 'walkforward';
    el('kind').dispatchEvent(new Event('change'));
    assert.equal(el('symbol').disabled, false);
    assert.equal(el('fold-count').disabled, false);
    assert.equal(el('grid-options').classList.contains('hidden'), false);
    el('fold-count').value = '3';
    el('window').value = 'anchored';
    el('symbol').value = 'AAA';
    el('rank-by').value = 'sharpe';
    el('grid-param-0').value = '1, 2';
    el('form').dispatchEvent(new Event('submit'));
    await tick();
    assert.equal(requests[1][0], '/backtest/walkforward');
    assert.deepEqual(requests[1][1].grid, { period: [1, 2] });
    assert.equal(requests[1][1].window, 'anchored');
    assertEscaped(el('advanced-table').innerHTML);
    assert.match(el('advanced-table').innerHTML, /<th>注記<\/th>/);
    assert.match(el('advanced-table').innerHTML, /先頭のパラメータを使用しました/);
    assert.match(el('robustness').textContent, /100%/);
    assert.match(el('chart').getAttribute('aria-label'), /OOS/);
    renderAdvancedResult({ ...data, contributions: { [attack]: 5 } }, 'portfolio');
    assertEscaped(el('advanced-table').innerHTML);
    assertAccessibleControls(readFileSync('frontend/index.html', 'utf8') + el('advanced-table').innerHTML);
});

test('saved result list opens a deep link, saves, edits and deletes with the in-app dialog', async () => {
    elements.clear();
    bindBacktestEvents();
    renderStrategies(registry);
    state.currentUser = { id: 1, access_token: 'saved' };
    let saved = { ...row };
    const calls = [];
    const edited = '更新済み';
    globalThis.fetch = async (url, options) => {
        const body = options.body && JSON.parse(options.body);
        calls.push([url, options.method, body]);
        if (url === '/backtest/run') return reply(data);
        if (options.method === 'POST') return reply(saved, 201);
        if (options.method === 'PATCH') return reply(saved = { ...saved, ...body });
        if (options.method === 'DELETE') {
            saved = null;
            return reply(null, 204);
        }
        return url.endsWith('/7') ? reply(saved) : reply({ runs: saved ? [saved] : [] });
    };
    const current = new URL('https://example.invalid/#/backtest?run=7');
    window.location = current;
    openBacktest(undefined, { run: 7 });
    await tick();
    assert.match(el('result-title').textContent, /AAA/);
    assert.match(el('saved-label').textContent, /クライアント/);
    assert.ok(el('saved-label').textContent.includes(attack));
    el('kind').value = 'single';
    el('symbol').value = 'AAA';
    el('strategy').value = 'momentum';
    el('param-0').value = '1';
    el('form').dispatchEvent(new Event('submit'));
    await tick();
    el('save').click();
    el('dialog-name').value = ' 記録 ';
    el('dialog-form').dispatchEvent(new Event('submit'));
    await tick();
    assert.equal(calls.find(([, method, body]) => method === 'POST' && body?.kind)[2].name, '記録');
    el('saved').open = true;
    el('saved').dispatchEvent(new Event('toggle'));
    await tick();
    assertEscaped(el('saved-table').innerHTML);
    assert.match(el('saved-table').innerHTML, /href="#\/backtest\?run=7"/);
    assertAccessibleControls(el('saved-table').innerHTML);
    const click = dataset => el('saved-table').dispatchEvent({
        type: 'click', target: { closest: () => ({ dataset }) },
    });
    click({ renameRun: '7' });
    assert.equal(el('dialog-name').value, attack);
    el('dialog-name').value = edited;
    el('dialog-form').dispatchEvent(new Event('submit'));
    await tick();
    assert.equal(calls.find(([, method]) => method === 'PATCH')[2].name, edited);
    assert.match(el('saved-table').innerHTML, /更新済み/);
    click({ deleteRun: '7' });
    assert.match(el('dialog-message').textContent, /更新済み/);
    assert.equal(calls.some(([, method]) => method === 'DELETE'), false);
    el('dialog-form').dispatchEvent(new Event('submit'));
    await tick();
    assert.equal(calls.some(([, method]) => method === 'DELETE'), true);
    assert.match(el('saved-table').innerHTML, /ありません/);
});

test('opening saved portfolio, walk-forward and grid runs restores the form and posts the same settings', async () => {
    elements.clear();
    bindBacktestEvents();
    renderStrategies(registry);
    state.currentUser = { id: 1, access_token: 'restore' };
    const common = { start_date: '2023-01-15', end_date: '2024-01-15', interval: '1wk', strategy: 'momentum',
        params: { period: 3 }, execution: 'close', fee_rate: 0.002, slippage_bps: 12, initial_capital: 555555,
        eps_lag_days: 105, risk_free: 0.02 };
    const rows = [
        { id: 20, kind: 'portfolio', request: { ...common, symbols: ['7203.T', '6758.T'],
            allocation: 'equal_active', rebalance_every: 21, rebalance_threshold_pct: 0 },
        result: { ...data, contributions: { '7203.T': 50 } } },
        { id: 21, kind: 'walkforward', request: { ...common, symbol: 'MSFT', grid: { period: [2, 4, 8] },
            rank_by: 'sharpe', folds: 4, window: 'anchored' },
        result: { ...data, folds: [], robustness: { rank_by: 'sharpe' } } },
        { id: 22, kind: 'grid', request: { ...common, symbol: 'AAPL', grid: { period: [2, 3] }, rank_by: 'sharpe' },
            result: { rank_by: 'sharpe', results: [] } },
    ];
    const requests = [];
    globalThis.fetch = async (url, options) => {
        if (options.method === 'POST') {
            requests.push([url, JSON.parse(options.body)]);
            return reply(rows.find(current => url.endsWith(current.kind)).result);
        }
        return reply(rows.find(current => url.endsWith(String(current.id))));
    };
    window.location = new URL('https://example.invalid/#/backtest');
    el('kind').value = 'walkforward';
    for (const saved of rows) {
        window.location.hash = `#/backtest?run=${saved.id}`;
        openBacktest(undefined, { run: saved.id });
        await tick();
        assert.equal(el('kind').value, saved.kind === 'grid' ? 'single' : saved.kind);
        assert.deepEqual(readBacktestRequest(), saved.request);
        el('form').dispatchEvent(new Event('submit'));
        await tick();
        assert.deepEqual(requests.at(-1), [`/backtest/${saved.kind}`, saved.request]);
    }
});

test('comparison aligns dates, normalizes each start to 100 and marks the best metrics', () => {
    const first = { ...row, summary: { ...row.summary, total_trades: 3, benchmark_return_pct: 7 } };
    const second = { ...row, id: 8, name: '検証2',
        summary: { return_pct: 40, max_drawdown: 2, sharpe: null, total_trades: 5, benchmark_return_pct: 10 },
        result: { equity_curve: [{ date: '2024-01-02', equity: 50 }, { date: '2024-01-04', equity: 70 }] } };
    const comparison = normalizedComparison([first, second]);
    assert.deepEqual(comparison.labels, ['2024-01-01', '2024-01-02', '2024-01-03', '2024-01-04']);
    assert.ok(comparison.datasets.every(series => series.spanGaps && series.pointRadius === 0 && !series.borderDash?.length));
    const [firstSeries, secondSeries] = comparison.datasets.map(series => series.data);
    assert.equal(firstSeries[0], 100);
    assert.equal(firstSeries[1], null);
    assert.ok(Math.abs(firstSeries[2] - 110) < 1e-10);
    assert.equal(firstSeries[3], null);
    assert.deepEqual(secondSeries, [null, 100, null, 140]);
    const html = comparisonTable([first, second]);
    assertEscaped(html);
    assert.match(html, /40% <span aria-label="最良">★/);
    assert.match(html, /最大ドローダウン<\/th><td>1% <span aria-label="最良">★/);
    assert.doesNotMatch(html, /— <span aria-label="最良">★/);
    assert.match(html, /取引回数（決済）<\/th><td>3<\/td><td>5<\/td>/);
    assert.match(html, /買い持ちリターン<\/th><td>7%<\/td><td>10%<\/td>/);
    assertAccessibleControls(renderSavedRuns([row, second]));
});
