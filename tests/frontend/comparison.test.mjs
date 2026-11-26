import assert from 'node:assert/strict';
import test from 'node:test';
import { fromRepo } from './helpers/dom-shim.mjs';
import { assertAccessibleControls } from './helpers/a11y.mjs';

const { alignComparison, bindComparisonEvents, collectComparison, comparisonMessages } = await fromRepo('frontend/js/comparison.js');
const { state } = await fromRepo('frontend/js/state.js');
const { normalizeSymbol } = await fromRepo('frontend/js/router.js');
const rows = (...entries) => entries.map(([date, close]) => ({ date, close }));
const byId = name => document.getElementById(`compare-${name}`);
const payload = '<img src=x onerror="alert(1)">';

test('alignment uses only shared dates and normalizes at the first common close', () => {
    const aligned = alignComparison([
        { symbol: 'AAPL', history: rows(['05', 75], ['02', 50], ['06', 60], ['08', 120]) },
        { symbol: '7203.T', history: rows(['02', 0], ['03', 10], ['06', 100], ['08', 50]) },
    ]);
    assert.deepEqual(aligned, {
        dates: ['06', '08'],
        series: [{ symbol: 'AAPL', values: [0, 100] }, { symbol: '7203.T', values: [0, -50] }],
    });
    assert.deepEqual(alignComparison([]), { dates: [], series: [] });
});

test('successful requests survive errors and all inline messages are escaped', async () => {
    const result = await collectComparison(['AAPL', payload, 'EMPTY'], async symbol => {
        if (symbol === payload) throw new Error(payload);
        return { history: symbol === 'EMPTY' ? [] : rows(['02', 100]) };
    });
    assert.deepEqual(result.series.map(series => series.symbol), ['AAPL']);
    assert.deepEqual(result.errors.map(item => item.symbol), [payload, 'EMPTY']);
    const html = comparisonMessages(result.errors);
    assert.doesNotMatch(html, /<img/);
    assert.match(html, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
});

test('watchlist suggestions, input limits, per-symbol errors, legend and removal work together', async () => {
    const sent = [];
    const chartConfigs = [];
    state.currentSymbol = 'AAPL';
    state.currentPeriod = '1mo';
    state.listItems = [{ symbol: '^N225' }, { symbol: '7203.T' }, { symbol: payload }];
    globalThis.Chart = class {
        constructor(_canvas, config) { this.config = config; chartConfigs.push(this); }
        destroy() { this.destroyed = true; }
    };
    globalThis.fetch = async path => {
        const symbol = decodeURIComponent(path.split('/')[2]);
        sent.push(symbol);
        const error = symbol === 'MISSING';
        return { ok: !error, status: error ? 429 : 200,
            json: async () => (error ? { detail: payload } : { history: rows(['02', 100], ['04', 150]) }) };
    };
    bindComparisonEvents();
    assert.match(byId('symbols').innerHTML, /\^N225/);
    assert.doesNotMatch(byId('symbols').innerHTML, /<img/);
    assert.equal(normalizeSymbol('  usdJPY=x '), 'USDJPY=X');
    for (const symbol of ['-AAPL', 'ß', 'AAPL?x', 'A'.repeat(21), payload]) assert.equal(normalizeSymbol(symbol), null);
    const add = value => {
        byId('symbol').value = value;
        byId('form').requestSubmit();
    };
    add(payload);
    assert.match(byId('input-error').textContent, /20文字/);
    add('aapl');
    assert.match(byId('input-error').textContent, /すでに/);
    for (const symbol of ['^n225', '7203.t', 'missing', 'brk.b']) add(symbol);
    add('MSFT');
    assert.match(byId('input-error').textContent, /4件/);
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(sent, ['AAPL', '^N225', '7203.T', 'MISSING', 'BRK.B']);
    assert.match(byId('errors').innerHTML, /MISSING.*&lt;img/);
    assert.doesNotMatch(byId('errors').innerHTML, /<img/);
    const chart = chartConfigs.at(-1);
    assert.deepEqual(chart.config.data.datasets.map(dataset => dataset.label), ['AAPL', '^N225', '7203.T', 'BRK.B']);
    assert.equal(chart.config.options.plugins.tooltip.callbacks.label({ dataset: { label: 'BRK.B' }, parsed: { y: 50 } }),
        'BRK.B +50.00%');
    assertAccessibleControls(byId('series').innerHTML);
    const click = new Event('click');
    Object.defineProperty(click, 'target', { value: { closest: () => ({ dataset: { compareRemove: 'BRK.B' } }) } });
    byId('series').dispatchEvent(click);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(chart.destroyed, true);
    assert.deepEqual(chartConfigs.at(-1).config.data.datasets.map(dataset => dataset.label), ['AAPL', '^N225', '7203.T']);
    assert.equal(document.activeElement, byId('symbol'));
});
