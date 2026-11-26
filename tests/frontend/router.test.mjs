import assert from 'node:assert/strict';
import test from 'node:test';
import { fromRepo } from './helpers/dom-shim.mjs';

globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ rate: 150 }) });
const { App } = await fromRepo('frontend/app.js');
const { parseRoute, serializeRoute } = await fromRepo('frontend/js/router.js');

test('backtest saved run deep links round-trip positive safe integer IDs', () => {
    assert.deepEqual(parseRoute('#/backtest?run=7'), { view: 'backtest', run: 7 });
    assert.equal(serializeRoute({ view: 'backtest', run: 7 }), '#/backtest?run=7');
    for (const invalid of ['-1', '0', '1e3', '3.5', '9007199254740992', '%3Cscript%3E']) {
        assert.deepEqual(parseRoute(`#/backtest?run=${invalid}`), { view: 'list' });
    }
});

test('routes round-trip with encoded symbols and the documented periods', () => {
    for (const [hash, route] of [
        ['#/', { view: 'list' }],
        ['#/stats', { view: 'stats' }],
        ['#/backtest', { view: 'backtest' }],
        ['#/backtest?symbol=USDJPY%3DX', { view: 'backtest', symbol: 'USDJPY=X' }],
        ['#/stock/BRK.B', { view: 'detail', symbol: 'BRK.B', period: '3mo' }],
        ['#/stock/%5EN225?period=1Y', { view: 'detail', symbol: '^N225', period: '1y' }],
        ['#/stock/7203.T?period=10Y', { view: 'detail', symbol: '7203.T', period: '10y' }],
    ]) {
        assert.deepEqual(parseRoute(hash), route);
        assert.equal(serializeRoute(route), hash);
    }
    assert.deepEqual(parseRoute('#/stock/aapl?period=1m'), { view: 'detail', symbol: 'AAPL', period: '1mo' });
});

test('malformed or unknown hashes return to the watchlist', () => {
    for (const hash of ['', '#not-a-route', '#/stock/', '#/stock/%FF', '#/stock/A%2FB',
        '#/stock/%3Csvg%3E', '#/stock/aapl?period=5Y', '#/stock/aapl?period=', '#/backtest?symbol=%00', '#/unknown']) {
        assert.deepEqual(parseRoute(hash), { view: 'list' }, hash);
    }
});

test('an invalid initial hash is replaced with the watchlist in the same history entry', () => {
    const existingState = { key: 'existing' };
    const url = 'https://local.invalid/?keep=1';
    for (const invalid of ['#/garbage/xx', '#/stock/%3Cimg%20src=x%3E', '#/portfolio']) {
        const location = new URL(url + invalid);
        const replacements = [];
        const history = {
            state: existingState,
            replaceState(state, _title, hash) {
                replacements.push({ state, hash });
                location.hash = hash;
            },
        };
        const app = new App({ windowRef: { location, history } });
        app.applyLocation();
        assert.equal(app.currentView, 'list');
        assert.equal(location.href, url + '#/');
        assert.deepEqual(replacements, [{ state: existingState, hash: '#/' }]);
        app.applyLocation();
        assert.equal(replacements.length, 1);
    }
});

test('initial deep link, back and forward select the route without duplicate history entries', async () => {
    const browser = new EventTarget();
    const entries = ['#/stock/%5EN225?period=1Y'];
    let index = 0;
    const changed = () => browser.dispatchEvent(new Event('hashchange'));
    browser.location = {
        get hash() { return entries[index]; },
        set hash(hash) {
            entries.splice(++index, entries.length - index, hash);
            changed();
        },
    };
    browser.history = {
        state: null,
        replaceState(_state, _title, hash) { entries[index] = hash; },
    };
    const events = new EventTarget();
    const viewDocument = {
        hidden: false, addEventListener: events.addEventListener.bind(events),
        getElementById: document.getElementById, querySelectorAll: () => [],
    };
    const calls = [];
    const state = { currentSymbol: null, currentPeriod: '3mo', currentUser: null };
    const noop = () => {};
    const app = new App({
        state, windowRef: browser, documentRef: viewDocument, fetchAPI: async () => ({}),
        loadStock(symbol) { state.currentSymbol = symbol; calls.push(['stock', symbol, state.currentPeriod]); },
        refreshDetailHistory() { calls.push(['period', state.currentPeriod]); },
        openBacktest(symbol) { calls.push(['backtest', symbol]); },
        bindAuthEvents: noop, bindListEvents: noop, bindDetailEvents: noop, bindChartEvents: noop,
        bindComparisonEvents: noop, bindFinancialChartEvents: noop, bindBacktestEvents: noop,
    });
    await app.init();
    assert.deepEqual(calls, [['stock', '^N225', '1y']]);
    app.switchView('detail', '^N225', '1y');
    assert.equal(entries.length, 1);
    app.switchView('detail', '^N225', '2y');
    app.switchView('backtest', '^N225');
    assert.deepEqual(entries, ['#/stock/%5EN225?period=1Y', '#/stock/%5EN225?period=2Y', '#/backtest?symbol=%5EN225']);
    index -= 2;
    changed();
    assert.equal(app.currentView, 'detail');
    assert.equal(state.currentPeriod, '1y');
    index++;
    changed();
    assert.equal(state.currentPeriod, '2y');
    events.dispatchEvent(new Event('accountchange'));
    state.currentUser = { id: 5, access_token: 'test' };
    events.dispatchEvent(new Event('accountchange'));
    assert.equal(browser.location.hash, '#/stock/%5EN225?period=2Y');
    index++;
    changed();
    assert.equal(app.currentView, 'backtest');
    assert.deepEqual(calls.map(call => call[0]), ['stock', 'period', 'backtest', 'period', 'period', 'stock', 'stock', 'backtest']);
    browser.location.hash = '#/invalid';
    assert.equal(app.currentView, 'list');
    assert.equal(browser.location.hash, '#/');
    assert.deepEqual(entries, ['#/stock/%5EN225?period=1Y', '#/stock/%5EN225?period=2Y', '#/backtest?symbol=%5EN225', '#/']);
});
