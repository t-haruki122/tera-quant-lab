import assert from 'node:assert/strict';
import test from 'node:test';
import { elements, fromRepo, storage } from './helpers/dom-shim.mjs';

const response = data => ({ ok: true, status: 200, json: async () => data });
globalThis.fetch = async () => response({ rate: 160 });
const { App } = await fromRepo('frontend/app.js');
const { cache, state } = await fromRepo('frontend/js/state.js');
const { openListAddModal, submitListAddFromModal } = await fromRepo('frontend/js/list.js');
const { renderStockHeader, showSection } = await fromRepo('frontend/js/detail.js');
const { renderChart } = await fromRepo('frontend/js/chart.js');
const tick = () => new Promise(resolve => setImmediate(resolve));
await tick();

const symbols = [
    { symbol: 'AAPL', name: 'Apple', price: 354.05, currency: 'USD', market_cap: 3e12 },
    { symbol: '7203.T', name: 'Toyota', price: 2450, currency: 'JPY', market_cap: 35e12 },
];
const yen = new Intl.NumberFormat('ja-JP', { style: 'currency', currency: 'JPY', maximumFractionDigits: 0 });
const byId = id => document.getElementById(id);

function makeApp(deps = {}) {
    elements.clear();
    cache.clear();
    storage.clear();
    Object.assign(state, {
        currentUser: null, currentListId: null, listItems: [], watchlists: [], defaultListId: null,
        exchangeRateUSDJPY: null, currentChartHistory: [], priceChart: null, rsiChartInstance: null,
    });
    const app = new App(deps);
    app.bindCoreEvents = () => {};
    return app;
}

function assertRows(converted) {
    const markup = byId('comparison-tbody').innerHTML;
    const apple = markup.match(/<tr data-symbol="AAPL">([\s\S]*?)<\/tr>/)?.[1];
    const toyota = markup.match(/<tr data-symbol="7203\.T">([\s\S]*?)<\/tr>/)?.[1];
    assert.ok(apple && toyota);
    assert.ok(apple.includes(converted ? yen.format(354.05 * 160) : '$354.05'));
    assert.match(toyota, converted ? /table-market-cap[^>]*>Mega</ : /table-market-cap[^>]*>—</);
}

for (const loggedIn of [true, false]) {
    for (const rateFirst of [true, false]) {
        test(`${loggedIn ? 'summary' : 'guest'} rows render correctly when ${rateFirst ? 'the rate' : 'stock data'} arrives first`, async () => {
            const app = makeApp();
            if (loggedIn) storage.set('stockAnalyzerUser', JSON.stringify({ id: 7, username: 'taro', access_token: 'test' }));
            const rate = Promise.withResolvers();
            const data = Promise.withResolvers();
            const calls = [];
            globalThis.fetch = async (path, options) => {
                calls.push(path);
                if (path === '/forex/usdjpy') {
                    assert.equal(options.headers.has('Authorization'), false);
                    await rate.promise;
                    return response({ rate: 160 });
                }
                if (path === '/user/7/lists') return response({ lists: [{ id: 8, name: 'マイリスト' }], default_list_id: 8 });
                await data.promise;
                if (path === '/user/7/lists/8/summary') return response({ items: symbols });
                const [, , symbol] = path.split('/');
                const item = symbols.find(entry => entry.symbol === symbol);
                assert.ok(item, path);
                return response(item);
            };

            const ready = app.init();
            const rowsReady = loggedIn ? null : (async () => {
                for (const { symbol } of symbols) {
                    openListAddModal(symbol);
                    await submitListAddFromModal();
                }
            })();
            await tick();
            assert.ok(calls.includes('/forex/usdjpy'));
            assert.ok(calls.includes(loggedIn ? '/user/7/lists/8/summary' : '/stock/AAPL'));

            if (rateFirst) {
                rate.resolve();
                await tick();
                assert.equal(state.exchangeRateUSDJPY, 160);
                assert.equal(byId('comparison-tbody').innerHTML.includes('Apple'), false);
                data.resolve();
            } else {
                data.resolve();
                await rowsReady;
                await tick();
                assert.equal(state.exchangeRateUSDJPY, null);
                assertRows(false);
                rate.resolve();
            }
            await Promise.all([ready, rowsReady]);
            assertRows(true);
            assert.equal(calls.length, loggedIn ? 3 : 7);
        });
    }
}

test('rate arrival and changes re-render the visible detail quote and chart from loaded data', async () => {
    let rate = 160;
    const calls = [];
    const app = makeApp({ loadStock: symbol => { state.currentSymbol = symbol; }, fetchAPI: async path => {
        calls.push(path);
        return { rate };
    } });
    for (const id of ['price-chart', 'rsi-chart']) byId(id).getContext = () => ({ clearRect() {} });
    globalThis.Chart = class {
        constructor(_ctx, config) { this.data = config.data; }
        destroy() {}
    };
    app.switchView('detail', 'AAPL');
    renderStockHeader({ symbol: 'AAPL', price: 354.05 }, { name: 'Apple', currency: 'USD' });
    showSection('stock-detail');
    state.currentChartHistory = [{ date: '2026-11-26', close: 350 }];
    state.currentChartCurrency = 'USD';
    renderChart(state.currentChartHistory, state.currentChartCurrency);
    assert.equal(byId('price-display').textContent, '$354.05');
    assert.deepEqual(state.priceChart.data.datasets[0].data, [350]);

    await app.fetchExchangeRate();
    assert.equal(byId('price-display').textContent, yen.format(354.05 * 160));
    assert.deepEqual(state.priceChart.data.datasets[0].data, [56_000]);
    rate = 150;
    await app.fetchExchangeRate();
    assert.equal(byId('price-display').textContent, yen.format(354.05 * 150));
    assert.deepEqual(state.priceChart.data.datasets[0].data, [52_500]);
    app.switchView('list');
    rate = 155;
    await app.fetchExchangeRate();
    assert.equal(byId('price-display').textContent, yen.format(354.05 * 150));
    app.switchView('detail');
    assert.equal(byId('price-display').textContent, yen.format(354.05 * 155));
    assert.deepEqual(state.priceChart.data.datasets[0].data, [54_250]);
    assert.equal(state.currentChartHistory[0].close, 350);
    assert.deepEqual(calls, ['/forex/usdjpy', '/forex/usdjpy', '/forex/usdjpy']);
});
