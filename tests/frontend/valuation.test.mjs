import assert from 'node:assert/strict';
import { beforeEach, test } from 'node:test';
import { assertAccessibleControls } from './helpers/a11y.mjs';
import { elements, fromRepo } from './helpers/dom-shim.mjs';

const { cache, state } = await fromRepo('frontend/js/state.js');
const { loadStock } = await fromRepo('frontend/js/detail.js');
const { loadPeers, renderValuationHistory } = await fromRepo('frontend/js/valuation.js');
const byId = id => document.getElementById(id);
const ok = data => ({ ok: true, status: 200, json: async () => data });

beforeEach(() => {
    elements.clear();
    cache.clear();
    state.currentUser = null;
});

test('JP peers render ROE while waiting for the price batch and display accessible ranks and encoded links', async () => {
    let releasePrices;
    const pricesReady = new Promise(resolve => { releasePrices = resolve; });
    const base = {
        sector_source: 'jpx33', sector_name: '電機 <img>', peer_count: 45, as_of: '2026-01-01',
        metrics: [
            { key: 'roe', label: 'ROE', value: 12.34, median: 8.12, rank: 12, count: 45, percentile: 75 },
            { key: 'equity_ratio', label: '<img onerror=alert(1)>', value: 55, median: 50, count: 3 },
        ], notes: ['J-Quants は 12 週間遅れ <script>'],
    };
    const requests = [];
    let signalPhaseTwo;
    const secondPhase = new Promise(resolve => { signalPhaseTwo = resolve; });
    globalThis.fetch = async path => {
        requests.push(path);
        if (path.endsWith('?prices=false')) return ok(base);
        signalPhaseTwo();
        return pricesReady;
    };
    const pending = loadPeers('/stock/7203.T', () => true);
    await secondPhase;
    const panel = byId('peers-body');
    assert.match(panel.innerHTML, /電機 &lt;img&gt;（JPX 33業種） · 45社/);
    assert.match(panel.innerHTML, /12\.34%[\s\S]+8\.12%[\s\S]+12位 \/ 45社/);
    assert.match(panel.innerHTML, /<meter[^>]+value="75"[^>]+aria-hidden="true"/);
    assert.match(panel.innerHTML, /パーセンタイル 75 \/ 100/);
    assert.match(panel.innerHTML, /比較対象が少ないため順位なし/);
    assert.match(panel.innerHTML, /株価を取得中/);
    assert.match(byId('peers-status').textContent, /株価を使う指標を読み込み中/);
    releasePrices(ok({ ...base, metrics: [...base.metrics, { key: 'per', label: 'PER', value: 19, median: 20, count: 40 }],
        peers: [{ symbol: 'PEER=X', name: '<svg onload=alert(1)>', per: 21 }, { symbol: 'X/<img>' }] }));
    await pending;
    assert.deepEqual(requests, ['/stock/7203.T/peers?prices=false', '/stock/7203.T/peers']);
    assert.match(panel.innerHTML, /19\.00倍/);
    assert.match(panel.innerHTML, /href="#\/stock\/PEER%3DX">PEER=X &lt;svg onload=alert\(1\)&gt;/);
    assert.match(panel.innerHTML, /&lt;script&gt;/);
    assert.doesNotMatch(panel.innerHTML, /<img|<script|<svg|株価を取得中/);
    assertAccessibleControls(panel.innerHTML);
});

test('valuation charts draw ranges and a distinct current marker with accessible summaries and fiscal exceptions', () => {
    const configs = [];
    let destroyed = 0;
    globalThis.Chart = class {
        constructor(_context, config) { configs.push(config); }
        destroy() { destroyed++; }
    };
    for (const key of ['per', 'pbr']) byId(`valuation-${key}-chart`).getContext = () => ({});
    const points = [
        { period_end: '2023-03-31', per: null, eps: -1, pbr: 0.8, source: 'yfinance' },
        { period_end: '2024-03-31', per: 10, pbr: 1, source: 'jquants' },
        { period_end: '2025-03-31', per: 12, pbr: null, bps: 0, source: 'jquants' },
    ];
    renderValuationHistory({ points, current: { per: 15, pbr: 1.25 },
        band: { per: { min: 10, median: 11, max: 12 }, pbr: { min: 0.8, median: 0.9, max: 1 } },
        notes: ['<img>'] });
    const panel = byId('valuation-history-body');
    assert.match(panel.innerHTML, /aria-describedby="valuation-per-summary"/);
    assert.match(panel.innerHTML, /現在 15\.00倍、最小 10\.00倍、中央値 11\.00倍、最大 12\.00倍。2023-03-31 赤字/);
    assert.match(panel.innerHTML, /2025-03-31 債務超過/);
    assert.match(panel.innerHTML, /Yahoo Finance/);
    assert.match(panel.innerHTML, /&lt;img&gt;/);
    assert.equal(configs.length, 2);
    assert.deepEqual(configs[0].data.datasets.map(s => s.data), [
        [null, 10, 12, null], [null, null, null, 15], [10, 10, 10, 10], [12, 12, 12, 12],
    ]);
    assert.equal(configs[0].data.datasets[1].pointStyle, 'rectRot');
    assertAccessibleControls(panel.innerHTML);
    renderValuationHistory({ points: [points[0]] });
    assert.equal(destroyed, 2);
    assert.match(panel.innerHTML, /グラフの表示には2期分のデータが必要です/);
    assert.doesNotMatch(panel.innerHTML, /<canvas/);
});

test('index detail hides the valuation cards and makes no valuation requests', async () => {
    const requests = [];
    for (const id of ['peers-card', 'valuation-history-card']) byId(id).classList.remove('hidden');
    globalThis.fetch = async path => {
        requests.push(path);
        if (path === '/stock/%5EN225') return ok({ symbol: '^N225', price: 123, unit: 'JPY', timestamp: '2026-01-01' });
        return ok({ news: [], history: [] });
    };
    await loadStock('^N225');
    for (const id of ['peers-card', 'valuation-history-card']) assert.ok(byId(id).classList.contains('hidden'));
    assert.ok(requests.includes('/stock/%5EN225'));
    assert.ok(requests.every(path => !/peers|valuation-history/.test(path)));
});
