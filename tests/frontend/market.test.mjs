import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { assertAccessibleControls } from './helpers/a11y.mjs';
import { fromRepo } from './helpers/dom-shim.mjs';

const { createMarketView } = await fromRepo('frontend/js/market.js');
const { formatMarketValue, formatMarketChange } = await fromRepo('frontend/js/market-format.js');
const { parseRoute } = await fromRepo('frontend/js/router.js');
const { loadStock } = await fromRepo('frontend/js/detail.js');

function testDocument() {
    const elements = new Map();
    return {
        ...document,
        getElementById(id) {
            if (!elements.has(id)) elements.set(id, document.createElement('div'));
            return elements.get(id);
        },
        addEventListener() {},
    };
}

const item = (overrides = {}) => ({
    symbol: '^N225', name: '日経平均', group: '日本', unit: 'points', value: 39_123.45, change: 123.45,
    change_pct: 0.32, as_of: '2026-01-01T00:00:00Z', sparkline: [40, 38, 41], ...overrides,
});
const cards = doc => doc.getElementById('market-groups').children.flatMap(group => group.children[1].children);
const flush = () => new Promise(resolve => setImmediate(resolve));

test('market values and changes use the display unit and accessible direction text', () => {
    for (const [value, unit, expected] of [
        [39_123.45, 'points', '39,123.45'],
        [155.23, 'jpy', '155.23円'],
        [2345.6, 'usd_per_oz', '$2,345.6/oz'],
        [75.2, 'usd_per_barrel', '$75.20/バレル'],
        [4.25, 'yield_pct', '4.25%'],
        [112_222.1, 'usd', '$112,222.10'],
        [null, 'points', '—'],
    ]) {
        assert.equal(formatMarketValue(value, unit), expected);
    }
    assert.deepEqual(formatMarketChange(item()), { text: '前日比 ▲ 上昇 +123.45（+0.32%）', direction: 'up' });
    assert.deepEqual(formatMarketChange(item({ unit: 'yield_pct', change_pt: -0.05 })), {
        text: '前日比 ▼ 下落 −0.05 pt', direction: 'down',
    });
    assert.match(formatMarketChange(item({ change: 0, change_pct: 0 })).text, /横ばい/);
    assert.equal(formatMarketChange(item({ change: null })).text, '前日比 —');
});

test('market cards render safe text, a named sparkline, stale badge and encoded detail links', async () => {
    const doc = testDocument();
    const payload = '<img src=x onerror=alert(1)>';
    const view = createMarketView({ documentRef: doc, request: async () => ({
        indicators: [
            item({ name: payload, group: payload, note: payload, error: payload, stale: true }),
            item({ symbol: 'JPY=X', name: 'ドル円', group: '為替', unit: 'jpy' }),
            item({ symbol: 'GC=F', name: '金', group: '商品', unit: 'usd_per_oz' }),
            item({ symbol: payload }),
        ],
    }) });
    await view.load();
    const links = cards(doc);
    assert.equal(links.length, 3);
    for (const [index, [hash, symbol]] of ['#/stock/%5EN225', '#/stock/JPY%3DX', '#/stock/GC%3DF']
        .map((hash, index) => [hash, ['^N225', 'JPY=X', 'GC=F'][index]]).entries()) {
        assert.equal(links[index].tagName, 'A');
        assert.equal(links[index].getAttribute('href'), hash);
        assert.equal(parseRoute(hash).symbol, symbol);
    }
    const [title, value, , change, chart, meta, note, error] = links[0].children;
    const group = doc.getElementById('market-groups').firstElementChild.firstElementChild;
    for (const target of [group, title, note, error]) {
        assert.equal(target.textContent, payload);
        assert.equal(target.innerHTML, '');
    }
    assert.equal(value.textContent, '39,123.45');
    assert.equal(change.dataset.direction, 'up');
    assert.match(change.textContent, /上昇/);
    const svg = chart.firstElementChild;
    assert.equal(svg.getAttribute('role'), 'img');
    assert.match(svg.getAttribute('aria-label'), /日次終値の推移/);
    assert.equal(svg.firstElementChild.getAttribute('points'), '2.00,10.00 60.00,26.00 118.00,2.00');
    assert.equal(svg.getAttribute('style'), null);
    assert.match(meta.children[0].textContent, /更新時刻/);
    assert.equal(meta.children[1].textContent, '古いデータ');
    assert.equal(meta.children[1].hidden, false);
    assert.equal(links[1].children[5].children[1].hidden, true);
});

test('cached data appears first, refresh updates the same card, and late responses are ignored', async () => {
    const doc = testDocument();
    const pending = [];
    const view = createMarketView({ documentRef: doc, request: (path, options) => new Promise(resolve => {
        pending.push({ path, options, finish: row => resolve({ indicators: [item(row)] }) });
    }) });
    const older = view.load();
    assert.equal(doc.getElementById('market-skeleton').hidden, false);
    assert.equal(pending[0].path, '/market/overview?cached=true');
    pending[0].finish({ value: null, sparkline: [] });
    await flush();
    const [link] = cards(doc);
    assert.equal(link.children[2].hidden, false);
    assert.equal(pending[1].path, '/market/overview?cached=false');

    const newer = view.load();
    pending[2].finish({ value: 100, stale: true });
    await flush();
    assert.equal(cards(doc)[0], link);
    assert.equal(link.children[1].textContent, '100.00');
    assert.equal(link.children[2].hidden, true);
    pending[3].finish({ value: 110 });
    await newer;
    pending[1].finish({ value: 999 });
    await older;
    assert.equal(cards(doc)[0], link);
    assert.equal(link.children[1].textContent, '110.00');
    assert.equal(link.children[5].children[1].hidden, true);
    assert.ok(pending.every(({ options }) => options.cache === 'no-store'));

    const abandoned = view.load();
    view.cancel();
    pending[4].finish({ value: 555 });
    await abandoned;
    assert.equal(pending.length, 5);
    assert.equal(link.children[1].textContent, '110.00');
});

test('market nav and manual update use named native buttons with the current-page state', async () => {
    globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ rate: 155 }) });
    const { App } = await fromRepo('frontend/app.js');
    const doc = testDocument();
    const location = new URL('https://local.invalid/#/');
    const calls = [];
    const noop = () => {};
    const app = new App({
        documentRef: doc, windowRef: { location },
        market: { load: () => calls.push('load'), cancel: () => calls.push('cancel') },
        bindAuthEvents: noop, bindListEvents: noop, bindDetailEvents: noop, bindChartEvents: noop,
        bindComparisonEvents: noop, bindFinancialChartEvents: noop, bindBacktestEvents: noop, bindAlertDialogEvents: noop,
    });
    const html = readFileSync('frontend/index.html', 'utf8');
    assertAccessibleControls(html);
    assert.match(html, /<button[^>]+id="btn-market-view"[^>]+type="button">経済指標<\/button>/);
    app.bindCoreEvents();
    const nav = doc.getElementById('btn-market-view');
    nav.click();
    assert.equal(location.hash, '#/market');
    assert.equal(nav.getAttribute('aria-current'), 'page');
    assert.ok(doc.getElementById('market-view').classList.contains('active'));
    doc.getElementById('market-refresh').click();
    app.switchView('list');
    assert.equal(nav.getAttribute('aria-current'), null);
    assert.deepEqual(calls, ['load', 'load', 'cancel']);
});

test('yield detail keeps the chart and hides company fundamentals', async () => {
    const requested = [];
    globalThis.fetch = async path => {
        requested.push(path);
        const data = path.includes('/history?') ? { unit: 'yield_pct', history: [
            { date: '2026-01-01', close: 4.2 }, { date: '2026-01-02', close: 4.25 },
        ] } : path.endsWith('/news') ? { news: [] }
            : { price: 4.25, name: '米10年国債利回り', symbol: '^TNX', unit: 'yield_pct' };
        return { ok: true, status: 200, json: async () => data };
    };
    const byId = id => document.getElementById(id);
    byId('price-chart').getContext = () => ({});
    let chart;
    globalThis.Chart = class {
        constructor(_context, config) { chart = config; }
        destroy() {}
    };
    await loadStock('^TNX');
    await flush();
    assert.match(byId('price-display').textContent, /^4\.25%$/);
    assert.match(byId('price-chart-summary').textContent, /4\.25%.*\+0\.05 pt/);
    assert.equal(chart.options.scales.y.ticks.callback(4.25), '4.25%');
    for (const name of ['profile', 'financials', 'indicators']) {
        assert.ok(byId(`${name}-card`).classList.contains('hidden'));
        assert.ok(!requested.some(path => path.includes(`/${name}`)));
    }
});
