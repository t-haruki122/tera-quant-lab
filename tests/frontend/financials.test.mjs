import assert from 'node:assert/strict';
import { beforeEach, test } from 'node:test';
import { elements, fromRepo } from './helpers/dom-shim.mjs';

const { cache, state } = await fromRepo('frontend/js/state.js');
const { formatLargeNumber } = await fromRepo('frontend/js/utils.js');
const { changeFinancialMetric, renderFinancialHistoryChart } = await fromRepo('frontend/js/financial-chart.js');
const { loadFinancials, renderIndicators } = await fromRepo('frontend/js/detail.js');
const byId = id => document.getElementById(id);
const netIncome = 205_000_000_000;

beforeEach(() => {
    elements.clear();
    cache.clear();
    state.exchangeRateUSDJPY = 161;
    state.financialChartInstance = null;
    byId('financials-history-chart').getContext = () => ({});
    globalThis.Chart = class {
        constructor(_context, config) { this.config = config; }
        destroy() {}
    };
});

test('large amounts convert explicit USD and preserve JPY, other currencies, and unknown currency', () => {
    assert.equal(formatLargeNumber(netIncome, 'JPY'), '¥2,050億');
    assert.equal(formatLargeNumber(netIncome, 'USD'), '¥33.01兆');
    assert.equal(formatLargeNumber(netIncome, null), '205,000,000,000');
    assert.equal(formatLargeNumber(netIncome), '205,000,000,000');
    assert.equal(formatLargeNumber(netIncome, 'EUR'), 'EUR 205,000,000,000');
    assert.equal(formatLargeNumber(null, 'JPY'), '—');

    state.exchangeRateUSDJPY = null;
    assert.equal(formatLargeNumber(netIncome, 'USD'), '$205.00B');
});

for (const [currency, expected, yen] of [
    ['JPY', '¥2,050億', ''],
    ['USD', '$205.00B', '（円換算 ¥33.01兆）'],
    ['EUR', 'EUR 205,000,000,000', ''],
    [null, '205,000,000,000', ''],
]) {
    test(`financial chart axis, tooltip and summary share the primary amount for ${currency}`, () => {
        renderFinancialHistoryChart({ currency, history: [{ period: '2024', net_income: netIncome }] });
        const { data, options } = state.financialChartInstance.config;
        const tooltip = options.plugins.tooltip.callbacks.label;
        assert.deepEqual(data.datasets[0].data, [netIncome]);
        assert.equal(options.scales.y.ticks.callback(netIncome), expected);
        assert.equal(tooltip({ parsed: { y: netIncome } }), `  純利益: ${expected}${yen}`);
        assert.equal(byId('financial-chart-summary').textContent, `2024年 純利益 ${expected}`);
        state.exchangeRateUSDJPY = null;
        assert.equal(tooltip({ parsed: { y: netIncome } }), `  純利益: ${expected}`);
    });
}

test('ADR financials and chart metric switch use statement currency even when the quote is USD', async () => {
    const financials = { currency: 'JPY', revenue: 1_250_000_000_000, net_income: netIncome };
    const payloads = {
        '/stock/TM/financials': financials,
        '/stock/TM/financials/history?limit=8': { currency: 'JPY', history: [{ ...financials, period: '2024' }] },
        '/stock/TM/dividends/history?limit=20': { history: [] },
    };
    globalThis.fetch = async path => {
        assert.ok(Object.hasOwn(payloads, path), path);
        return { ok: true, status: 200, json: async () => payloads[path] };
    };

    await loadFinancials('TM', { currency: 'USD' });
    const html = byId('financials-summary').innerHTML;
    assert.match(html, /売上高<\/span><span class="data-value">¥1\.25兆<\/span>/);
    assert.match(html, /純利益<\/span><span class="data-value">¥2,050億<\/span>/);
    changeFinancialMetric('revenue');
    const { options } = state.financialChartInstance.config;
    assert.equal(options.plugins.tooltip.callbacks.label({ parsed: { y: financials.revenue } }), '  売上高: ¥1.25兆');
    assert.equal(byId('metric-revenue').getAttribute('aria-pressed'), 'true');
});

test('D/E ratios display their percentage unit with the existing rating thresholds', () => {
    for (const [value, rating] of [[49.99, 'good'], [50, 'neutral'], [133.16, 'neutral'], [150, 'bad']]) {
        renderIndicators({ debt_to_equity: value });
        const row = byId('indicators-body').innerHTML.split('D/Eレシオ').at(-1);
        assert.ok(row.includes(`${value}%`));
        assert.ok(row.includes(`indicator-badge ${rating}`));
    }
});
