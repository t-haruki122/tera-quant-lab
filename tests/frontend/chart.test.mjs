import assert from 'node:assert/strict';
import test from 'node:test';
import { fromRepo, storage } from './helpers/dom-shim.mjs';

const { bindChartEvents, buildChartDatasets, candlestickDatasets, changeChartType, loadChart,
    readChartType, renderChart, resetPriceChart } = await fromRepo('frontend/js/chart.js');
const { state } = await fromRepo('frontend/js/state.js');
const byId = id => document.getElementById(id);
const history = [
    { date: '2026-01-02', open: 4, high: 9, low: 3, close: 8, volume: 10,
        sma_50: 5, bb_upper: 10, bb_middle: 6, bb_lower: 2, macd: 1.5, macd_signal: 1, macd_hist: 0.5, rsi_14: 65 },
    { date: '2026-01-05', open: 8, high: 10, low: 2, close: 5, volume: 0,
        sma_50: 5, bb_upper: 9, bb_middle: 5, bb_lower: 1, macd: -1, macd_signal: 0, macd_hist: -1, rsi_14: 42 },
    { date: '2026-01-06', open: 5, high: 5, low: 5, close: 5 },
    { date: '2026-01-07', open: null, high: null, low: null, close: 5 },
];
const colors = { up: '#10b981', down: '#f43f5e', background: '#111827' };

test('candles use overlaid floating bars, direction colors and a visible doji', () => {
    const [wick, body] = candlestickDatasets(history, 2, colors);
    assert.deepEqual(wick.data, [[6, 18], [4, 20], [10, 10], null]);
    assert.deepEqual(body.data, [[8, 16], [16, 10], [10, 10], null]);
    assert.deepEqual(wick.backgroundColor, [colors.up, colors.down, colors.up, colors.up]);
    assert.deepEqual(body.borderColor, wick.backgroundColor);
    assert.deepEqual(body.backgroundColor.slice(0, 3), [colors.background, colors.down, colors.background]);
    assert.equal(wick.grouped, false);
    assert.equal(body.grouped, false);
    assert.ok(body.minBarLength > 0);
});

test('line, SMA, RSI, Bollinger, volume and MACD datasets use the same records', () => {
    const enabled = new Set(['sma50', 'bollinger', 'rsi', 'volume', 'macd']);
    const { price, rsi, volume, macd } = buildChartDatasets(history, { type: 'line', enabled, scale: 2, colors });
    assert.deepEqual(price.map(dataset => dataset.data), [
        [16, 10, 10, 10], [10, 10, null, null], [4, 2, null, null], [20, 18, null, null], [12, 10, null, null],
    ]);
    assert.equal(price[3].fill, '-1');
    assert.ok(price[4].borderDash.length);
    assert.deepEqual(rsi[0].data, [65, 42, null, null]);
    assert.deepEqual(volume[0].data, [10, 0, null, null]);
    assert.equal(volume[0].type, 'bar');
    assert.deepEqual(macd.map(dataset => [dataset.type, dataset.data]), [
        ['line', [3, -2, null, null]], ['line', [2, 0, null, null]], ['bar', [1, -2, null, null]],
    ]);
});

function chartRecorder() {
    const charts = [];
    for (const key of ['price', 'rsi', 'volume', 'macd']) {
        byId(`${key}-chart`).getContext = () => key;
        if (key !== 'price') byId(`toggle-${key}`).checked = true;
    }
    globalThis.Chart = class {
        constructor(canvas, config) { Object.assign(this, { canvas, config, destroyed: false }); charts.push(this); }
        destroy() { assert.equal(this.destroyed, false); this.destroyed = true; }
    };
    return charts;
}

test('chart type buttons persist the choice, update accessible state and replace every chart', () => {
    const charts = chartRecorder();
    const line = byId('line-button');
    line.dataset.chartType = 'line';
    const candle = byId('candle-button');
    candle.dataset.chartType = 'candlestick';
    document.querySelectorAll = selector => selector === '[data-chart-type]' ? [line, candle] : [];
    storage.clear();
    assert.equal(readChartType(), 'line');
    bindChartEvents();
    state.currentChartHistory = history;
    state.currentChartCurrency = 'USD';
    renderChart(history, 'USD');
    assert.equal(line.getAttribute('aria-pressed'), 'true');
    candle.click();
    assert.equal(readChartType(), 'candlestick');
    assert.equal(candle.getAttribute('aria-pressed'), 'true');
    assert.ok(charts.slice(0, 4).every(chart => chart.destroyed));
    assert.ok(charts.slice(4).every(chart => !chart.destroyed));
    for (const chart of charts.slice(4)) assert.deepEqual(chart.config.data.labels, history.map(row => row.date));
    const { dataset, dataIndex } = { dataset: charts[4].config.data.datasets[1], dataIndex: 1 };
    const tooltip = charts[4].config.options.plugins.tooltip.callbacks;
    assert.deepEqual(tooltip.label({ dataset, dataIndex }), ['▼ 始値 $8.00', '高値 $10.00', '安値 $2.00', '終値 $5.00']);
    resetPriceChart();
    assert.ok(charts.every(chart => chart.destroyed));

    const saved = globalThis.localStorage;
    try {
        globalThis.localStorage = { getItem() { throw new Error('storage blocked'); }, setItem() { throw new Error('storage blocked'); } };
        assert.equal(readChartType(), 'line');
        changeChartType('line');
        assert.equal(line.getAttribute('aria-pressed'), 'true');
    } finally {
        globalThis.localStorage = saved;
    }
});

test('10Y candles request weekly history and a late daily response cannot replace them', async () => {
    const charts = chartRecorder();
    const requests = [];
    state.currentSymbol = 'TEST';
    state.currentUser = null;
    globalThis.fetch = url => new Promise(resolve => requests.push({
        interval: new URL(url, 'https://local.invalid').searchParams.get('interval'),
        finish(rows) { resolve({ ok: true, status: 200, json: () => ({ history: rows }) }); },
    }));
    const daily = loadChart('TEST', '10y', 'USD');
    changeChartType('candlestick');
    assert.deepEqual(requests.map(request => request.interval), ['1d', '1wk']);
    requests[1].finish(history.slice(0, 2));
    await new Promise(resolve => setImmediate(resolve));
    requests[0].finish(history);
    await daily;
    assert.equal(charts.length, 4);
    assert.equal(charts[0].config.data.labels.length, 2);
    assert.match(byId('chart-history-status').textContent, /週足/);
    resetPriceChart();
    assert.ok(charts.every(chart => chart.destroyed));
});
