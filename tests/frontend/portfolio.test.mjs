import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { assertAccessibleControls } from './helpers/a11y.mjs';
import { elements, fromRepo, pressKey } from './helpers/dom-shim.mjs';

const {
    bindPortfolioEvents, buildComposition, refreshPortfolioSession, renderHoldingTable, renderPortfolioTotals,
} = await fromRepo('frontend/js/portfolio.js');
const { renderStockHeader } = await fromRepo('frontend/js/detail.js');
const { parseRoute, serializeRoute } = await fromRepo('frontend/js/router.js');
const { state } = await fromRepo('frontend/js/state.js');
const byId = id => document.getElementById(id);
const hidden = id => byId(id).classList.contains('hidden');
const tick = () => new Promise(resolve => setImmediate(resolve));
const reply = (data, status = 200) => ({ ok: status < 400, status, json: async () => data });
const holding = {
    id: 1, symbol: 'AAPL', name: '会社 <img src=x onerror=alert(1)>', currency: 'USD', shares: 2, avg_cost: 100,
    price: 120, market_value_jpy: 36_000.11, unrealized_pl_jpy: 6_000.42, unrealized_pl_pct: 20, weight_pct: 60,
    day_change_pct: 0.4, note: '注意 <script>確認</script>',
};
const losing = {
    id: 2, symbol: '7203.T', currency: 'JPY', shares: 6, avg_cost: 3_000, price: 4_000, market_value_jpy: 24_000,
    unrealized_pl_jpy: -6_000, unrealized_pl_pct: -20, weight_pct: 40,
};
const totals = { market_value_jpy: 906_417.12, cost_basis_jpy: 618_194.01, unrealized_pl_jpy: 288_223.11, unrealized_pl_pct: 46.62 };
const chartData = [holding, losing, { symbol: 'UNKNOWN', market_value_jpy: null }];

test('renders escaped holdings, a stock deep link, signed P/L, totals and sortable headers', () => {
    const symbol = byId('symbol-header');
    symbol.dataset.sort = 'symbol';
    const pnl = byId('pnl-header');
    pnl.dataset.sort = 'unrealized_pl_jpy';
    const sortButton = byId('pnl-sort');
    const sortArrow = byId('pnl-sort-arrow');
    pnl.querySelector = selector => selector === 'button' ? sortButton : sortArrow;
    const original = document.querySelectorAll;
    state.exchangeRateUSDJPY = 150;
    document.querySelectorAll = selector => selector === '#portfolio-table th[data-sort]' ? [symbol, pnl] : [];
    try {
        bindPortfolioEvents();
        renderHoldingTable([holding, losing]);
        const table = byId('portfolio-holdings').innerHTML;
        assert.match(table, /<a href="#\/stock\/AAPL">AAPL<\/a>/);
        assert.match(table, /&lt;img src=x onerror=alert\(1\)&gt;/);
        assert.match(table, /&lt;script&gt;確認&lt;\/script&gt;/);
        assert.doesNotMatch(table, /<(?:img|script)\b/);
        assert.match(table, /<td>\$100\.00<\/td>\s*<td>\$120\.00<\/td>/);
        assert.match(table, /<td>￥3,000<\/td>\s*<td>￥4,000<\/td>/);
        assert.match(table, /<td>￥36,000<\/td>/);
        assert.match(table, /class="portfolio-gain">▲&nbsp;\+￥6,000<\/span>/);
        assert.match(table, /class="portfolio-loss">▼&nbsp;−￥6,000<\/span>/);
        assert.match(table, /▲&nbsp;\+20%/);
        assertAccessibleControls(table);
        assert.equal(symbol.getAttribute('aria-sort'), 'ascending');
        sortButton.click();
        assert.equal(symbol.getAttribute('aria-sort'), null);
        assert.equal(pnl.getAttribute('aria-sort'), 'ascending');
        assert.equal(sortArrow.textContent, '▲');
        sortButton.click();
        assert.equal(pnl.getAttribute('aria-sort'), 'descending');
        assert.equal(sortArrow.textContent, '▼');
        renderPortfolioTotals(totals);
        const cards = byId('portfolio-totals').innerHTML;
        assert.match(cards, /評価額[\s\S]*￥906,417[\s\S]*取得額[\s\S]*￥618,194/);
        assert.match(cards, /class="portfolio-gain">▲&nbsp;\+￥288,223<\/span>/);
        assert.match(cards, /▲&nbsp;\+46\.62%/);
    } finally {
        document.querySelectorAll = original;
        state.exchangeRateUSDJPY = null;
    }
});

test('portfolio deep link gates requests at login and renders a named doughnut and text summary', async () => {
    elements.clear();
    bindPortfolioEvents();
    const calls = [];
    const charts = [];
    const colors = ['#60a5fa', '#22d3ee'];
    globalThis.getComputedStyle = () => ({
        getPropertyValue(name) {
            return name === '--bg-card' ? '#181c26' : (colors[Number(name.at(-1)) - 1] ?? '');
        },
    });
    globalThis.Chart = class {
        constructor(canvas, config) { charts.push({ canvas, config }); }
        destroy() {}
    };
    globalThis.fetch = async (url) => {
        calls.push(url);
        return url.endsWith('/alerts')
            ? reply({ alerts: [{ id: 9, symbol: 'AAPL', kind: 'above', threshold: 9, enabled: true, note: '<tag>' }] })
            : reply({ holdings: chartData.slice(0, 2), totals, partial: true });
    };
    assert.deepEqual(parseRoute('#/portfolio'), { view: 'portfolio' });
    assert.equal(serializeRoute(parseRoute('#/portfolio')), '#/portfolio');
    const { App } = await fromRepo('frontend/app.js');
    const app = new App({ refreshPortfolioSession });
    app.renderRoute(parseRoute('#/portfolio'));
    assert.equal(app.currentView, 'portfolio');
    assert.equal(byId('btn-portfolio-view').getAttribute('aria-current'), 'page');
    assert.equal(hidden('portfolio-login'), false);
    assert.equal(hidden('portfolio-content'), true);
    assert.deepEqual(calls, ['/forex/usdjpy']);
    byId('portfolio-login-btn').click();
    assert.equal(hidden('auth-modal-overlay'), false);
    const { closeAuthModal } = await fromRepo('frontend/js/auth.js');
    closeAuthModal();

    state.currentUser = { id: 7, access_token: 'test' };
    await refreshPortfolioSession(true);
    assert.equal(hidden('portfolio-login'), true);
    assert.equal(hidden('portfolio-content'), false);
    assert.deepEqual(calls.slice(1), ['/user/7/portfolio', '/user/7/alerts']);
    assert.equal(charts[0].config.type, 'doughnut');
    assert.deepEqual(charts[0].config.data, buildComposition(chartData, colors, '#181c26'));
    assert.deepEqual(charts[0].config.data.datasets[0], {
        data: [36_000.11, 24_000], backgroundColor: colors, borderColor: '#181c26',
    });
    assert.match(byId('portfolio-chart-summary').textContent, /AAPL ￥36,000（60%）/);
    assert.equal(byId('portfolio-legend').innerHTML, '<li>AAPL 60%</li><li>7203.T 40%</li>');
    assert.match(byId('portfolio-status').textContent, /一部の銘柄/);
    assert.match(byId('portfolio-alerts').innerHTML, /&lt;tag&gt;/);
    assertAccessibleControls(byId('portfolio-alerts').innerHTML);
    const html = readFileSync('frontend/index.html', 'utf8');
    assert.match(html, /id="portfolio-chart" role="img" aria-label="[^"]+" aria-describedby="portfolio-chart-summary"/);
    state.currentUser = null;
    refreshPortfolioSession(true);
    assert.equal(byId('portfolio-holdings').innerHTML, '');
    assert.equal(hidden('portfolio-content'), true);
    delete globalThis.Chart;
    delete globalThis.getComputedStyle;
});

test('detail buttons prefill native prices, holding validation guards IME and duplicate offers edit', async () => {
    elements.clear();
    bindPortfolioEvents();
    state.currentUser = { id: 7, access_token: 'test' };
    refreshPortfolioSession(false);
    renderStockHeader({ symbol: 'AAPL', price: 123.4, timestamp: '2026-11-25T08:00:00Z' }, { currency: 'USD' });
    const requests = [];
    globalThis.fetch = async (url, options) => {
        requests.push([url, options.method ?? 'GET', options.body && JSON.parse(options.body)]);
        if (options.method === 'POST' && url.endsWith('portfolio')) return reply({ detail: 'この銘柄は登録済みです。' }, 409);
        return reply({ holdings: [holding] });
    };
    byId('btn-detail-alert').click();
    assert.equal(byId('alert-symbol').value, 'AAPL');
    assert.equal(byId('alert-threshold').value, '123.4');
    byId('alert-threshold').value = '';
    byId('alert-editor-form').requestSubmit();
    await tick();
    assert.match(byId('alert-editor-error').textContent, /基準値は0より大きい/);
    byId('alert-editor-cancel').click();

    byId('btn-detail-holding').click();
    const overlay = byId('holding-editor-overlay');
    const shares = byId('holding-shares');
    const avg = byId('holding-avg-cost');
    assert.equal(byId('holding-symbol').value, 'AAPL');
    assert.equal(avg.value, '123.4');
    shares.value = '0';
    pressKey(overlay, shares, 'Enter');
    await tick();
    assert.match(byId('holding-editor-error').textContent, /保有数量は0より大きい/);
    shares.value = '2';
    avg.value = '-1';
    pressKey(overlay, avg, 'Enter');
    await tick();
    assert.match(byId('holding-editor-error').textContent, /平均取得単価は0以上/);
    avg.value = '123.4';
    pressKey(overlay, shares, 'Enter', { isComposing: true });
    pressKey(overlay, shares, 'Enter', { keyCode: 229 });
    assert.deepEqual(requests, []);
    pressKey(overlay, shares, 'Enter');
    await tick();
    assert.match(byId('holding-editor-error').textContent, /登録済み/);
    assert.equal(hidden('holding-edit-existing'), false);
    byId('holding-edit-existing').click();
    await tick();
    assert.equal(byId('holding-symbol').readOnly, true);
    assert.equal(avg.value, '100');
    assert.equal(byId('holding-editor-title').textContent, '保有銘柄を編集');
    shares.value = '3';
    avg.value = '0';
    pressKey(overlay, shares, 'Enter');
    await tick();
    assert.deepEqual(requests, [
        ['/user/7/portfolio', 'POST', { shares: 2, avg_cost: 123.4, acquired_on: null, note: null, symbol: 'AAPL' }],
        ['/user/7/portfolio', 'GET', undefined],
        ['/user/7/portfolio/1', 'PUT', { shares: 3, avg_cost: 0, acquired_on: null, note: holding.note }],
    ]);
    assert.equal(hidden('holding-editor-overlay'), true);
    assert.equal(document.activeElement, byId('btn-detail-holding'));
});
