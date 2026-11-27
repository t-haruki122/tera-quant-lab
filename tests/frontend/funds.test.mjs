import assert from 'node:assert/strict';
import test from 'node:test';
import { assertAccessibleControls } from './helpers/a11y.mjs';
import { fromRepo } from './helpers/dom-shim.mjs';

const { renderListTable } = await fromRepo('frontend/js/list.js');
const { loadStock, refreshDetailHistory } = await fromRepo('frontend/js/detail.js');
const { normalizeLookupSymbol, parseRoute } = await fromRepo('frontend/js/router.js');
const { state } = await fromRepo('frontend/js/state.js');
const byId = id => document.getElementById(id);
const symbol = 'FUND-0331418A';
const source = 'データ: 三菱UFJアセットマネジメント 投信情報API';
const unsafeName = '<img src=x onerror=alert(1)>オルカン';

test('fund rows use NAV and units and display escaped allocation and fund names', () => {
    state.currentUser = { id: 1 };
    state.listItems = [{
        symbol, name: unsafeName, price: 35610, currency: 'JPY', shares: 15000, value_jpy: 53415,
        pending: true, market_cap: 1e12, per: -3, pbr: -4, mix_index: 12, roe: 12, dividend_yield: 3,
    }];
    state.listPortfolio = {
        total_value_jpy: 53415, cash_jpy: 0, cash_ratio_pct: 0,
        by_region: [], by_currency: [], by_sector: [], excluded: [], concentration: {},
        by_asset_class: [{ label: '投資信託', value_jpy: 53415, pct: 100 }, { label: unsafeName, value_jpy: 0, pct: 0 }],
    };
    renderListTable();
    const row = byId('comparison-tbody').innerHTML;
    assertAccessibleControls(row);
    assert.match(row, /FUND-0331418A の口数/);
    assert.match(row, /aria-describedby="shares-error-FUND-0331418A shares-FUND-0331418A-help"/);
    assert.match(row, /投資信託は口数を入力/);
    assert.match(row, /35,610.*10,000口あたり/);
    assert.match(row, /53,415/);
    assert.match(row, /<td class="table-market-cap" title="">—<\/td>(\s*<td class="numeric">—<\/td>){3}/);
    assert.doesNotMatch(row, /indicator-badge|債務超過|赤字|<img/);
    assert.match(row, /&lt;img/);
    const allocation = byId('watchlist-portfolio').innerHTML;
    assert.match(allocation, /資産クラス[\s\S]*投資信託[\s\S]*53,415 円/);
    assert.match(allocation, /&lt;img/);
    assert.doesNotMatch(allocation, /<img/);
    assert.equal(byId('watchlist-fund-source').classList.contains('hidden'), false);
});

test('fund detail loads only the fund endpoint, escapes stats and charts accumulated daily values', async () => {
    assert.equal(normalizeLookupSymbol('０３３１４１８Ａ'), symbol);
    assert.equal(parseRoute(`#/stock/${symbol}`).symbol, symbol);
    assert.equal(parseRoute('#/stock/FUND-0331418').view, 'list');

    const history = [{ base_date: '2026-11-27', nav: 35610 }];
    const payload = {
        symbol, association_code: '0331418A', name: unsafeName, base_date: '2026-11-27', nav: 35610,
        change: 246, change_pct: 0.7, net_assets: 13649778246815, source_credit: source, history,
        stats: {
            returns: { '1m': -4.89 }, risk: { '1y': 13.61 }, risk_return: { '1y': 0.71 },
            max_nav: { '1m': { nav: 37902, date: unsafeName } }, min_nav: { full: { nav: 8102, date: '2020-03-24' } },
        },
    };
    const paths = [];
    globalThis.fetch = async path => {
        paths.push(path);
        return { ok: true, status: 200, json: async () => payload };
    };

    await loadStock(symbol);
    refreshDetailHistory();
    assert.deepEqual(paths, [`/stock/${symbol}/fund`]);
    assert.equal(byId('company-name').textContent, unsafeName);
    assert.match(byId('stock-meta').textContent, /協会コード 0331418A/);
    assert.match(byId('price-display').textContent, /基準価額 35,610 円（1万口あたり）/);
    assert.match(byId('price-timestamp').textContent, /基準日 2026-11-27/);
    assert.match(byId('fund-change').textContent, /\+246 円（\+0.7%）/);
    assert.match(byId('fund-assets').textContent, /13.65兆/);
    assert.equal(byId('fund-source').textContent, source);
    assert.equal(byId('fund-panels').classList.contains('hidden'), false);
    for (const id of ['stock-chart-card', 'compare-card', 'profile-card', 'financials-card', 'indicators-card',
        'peers-card', 'valuation-history-card', 'news-card', 'btn-detail-alert', 'btn-detail-backtest']) {
        assert.equal(byId(id).classList.contains('hidden'), true, id);
    }
    const stats = byId('fund-stats-body').innerHTML;
    for (const label of ['1か月', '3か月', '6か月', '1年', '3年', '設定来', '-4.89%', '13.61%', '0.71', '37,902 円', '2020-03-24']) {
        assert.ok(stats.includes(label), label);
    }
    assert.match(stats, /&lt;img/);
    assert.doesNotMatch(stats, /<img/);
    assert.equal(byId('fund-chart-status').textContent, '日々の基準価額を蓄積中です');
    assert.equal(byId('fund-chart-container').classList.contains('hidden'), true);

    byId('fund-chart').getContext = () => ({});
    let config, destroyed = false;
    globalThis.Chart = class {
        constructor(_context, options) { config = options; }
        destroy() { destroyed = true; }
    };
    try {
        history.push({ base_date: '2026-11-30', nav: 36000 });
        await loadStock(symbol);
        assert.equal(byId('fund-chart-container').classList.contains('hidden'), false);
        assert.equal(byId('fund-chart-status').textContent, '');
        assert.match(byId('fund-chart-summary').textContent, /2026-11-30 36,000 円/);
        assert.deepEqual(config.data.labels, ['2026-11-27', '2026-11-30']);
        assert.deepEqual(config.data.datasets[0].data, [35610, 36000]);
        history.pop();
        await loadStock(symbol);
        assert.ok(destroyed);
    } finally {
        delete globalThis.Chart;
    }
});
