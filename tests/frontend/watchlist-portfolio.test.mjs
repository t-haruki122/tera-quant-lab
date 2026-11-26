import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { assertAccessibleControls } from './helpers/a11y.mjs';
import { elements, fromRepo, pressKey } from './helpers/dom-shim.mjs';

const { state, cache } = await fromRepo('frontend/js/state.js');
const { updateAccountUI } = await fromRepo('frontend/js/auth.js');
const { bindListEvents, selectWatchlist, resetListSession, sortTable } = await fromRepo('frontend/js/list.js');

const byId = id => document.getElementById(id);
const markup = () => byId('comparison-tbody').innerHTML;
const tick = () => new Promise(resolve => setImmediate(resolve));
const response = (data, status = 200) => ({ ok: status === 200, status, json: async () => data });
const root = '/user/7/lists';
const summary = id => `${root}/${id}/summary?cached=true`;
const html = readFileSync('frontend/index.html', 'utf8');

test('dashboard subtitle follows sign-in and sign-out', () => {
    const guest = 'ログインすると、株数や現金を入力して評価額と構成比を確認できます。';
    assert.ok(html.includes(guest));
    state.currentUser = { id: 7, username: 'alice' };
    updateAccountUI();
    assert.equal(byId('list-description').textContent, '株数と現金を入力すると、評価額と構成比を表示します。');
    state.currentUser = null;
    updateAccountUI();
    assert.equal(byId('list-description').textContent, guest);
});

function reset() {
    elements.clear();
    cache.clear();
    state.currentUser = { id: 7, access_token: 'test' };
    resetListSession();
    state.watchlists = [{ id: 8, name: '第一' }, { id: 9, name: '第二' }];
    state.sortKey = 'symbol';
    state.sortAsc = true;
    bindListEvents();
}

const securityText = '<img src=x>';
const portfolio = {
    total_value_jpy: 10000, stocks_value_jpy: 7500, cash_jpy: 2500, cash_ratio_pct: 25,
    by_region: [{ label: '日本株', value_jpy: 7500, pct: 75 }, { label: '現金', value_jpy: 2500, pct: 25 }],
    by_currency: [{ label: 'JPY', value_jpy: 10000, pct: 100 }],
    by_sector: [{ label: securityText, value_jpy: 7500, pct: 100 }],
    concentration: { largest: { symbol: '9432.T', pct: 75 }, top_5_pct: 75, holding_count: 2 },
    usd_jpy: { rate: 150, stale: true, cached_at: '2026-11-25T00:00:00Z' },
    excluded: [{ symbol: 'EURTEST', reason: securityText }],
};

test('watchlist renders sortable holdings columns, escaped ratios and text alongside bars', async () => {
    reset();
    const items = [
        { symbol: '9432.T', name: 'NTT', shares: 3.125, value_jpy: 7500, weight_pct: 75, tags: ['秘密'], memo: '注記' },
        { symbol: 'EURTEST', shares: 1, value_jpy: null, weight_pct: null },
        { symbol: 'AAPL', shares: null, value_jpy: null, weight_pct: null },
    ];
    globalThis.fetch = async path => {
        assert.equal(path, summary(8));
        return response({ items, cash_jpy: 2500, portfolio });
    };
    await selectWatchlist(8);
    const table = html.match(/<table id="comparison-table">[\s\S]*?<\/table>/)[0];
    assert.match(table, /株数[\s\S]*評価額（円）[\s\S]*比率[\s\S]*操作/);
    assert.doesNotMatch(table, /タグ|メモ/);
    assert.match(markup(), /for="shares-9432\.T">9432\.T の株数<\/label>/);
    assert.match(markup(), /value="3\.125"/);
    assert.match(markup(), />7,500<\/td>[\s\S]*>75\.00%<\/td>/);
    assert.doesNotMatch(markup(), /秘密|注記/);
    for (const key of ['shares', 'value_jpy', 'weight_pct']) {
        sortTable(key);
        sortTable(key);
        const ordered = [...markup().matchAll(/<tr data-symbol="([^"]+)"/g)].map(match => match[1]);
        assert.equal(ordered[0], '9432.T');
        if (key === 'shares') assert.equal(ordered.at(-1), 'AAPL');
    }
    const cards = byId('watchlist-portfolio');
    assert.equal(cards.classList.contains('hidden'), false);
    assert.match(cards.innerHTML, /評価額合計[\s\S]*10,000 円/);
    assert.match(cards.innerHTML, /日本株[\s\S]*7,500 円 · 75\.00%[\s\S]*<meter aria-hidden="true"[^>]*value="75"/);
    assert.match(cards.innerHTML, /円（JPY）[\s\S]*100\.00%/);
    assert.match(cards.innerHTML, /最大保有比率[\s\S]*9432\.T · 75\.00%[\s\S]*上位5銘柄比率/);
    assert.match(cards.innerHTML, /USD\/JPY[\s\S]*1ドル = 150 円[\s\S]*キャッシュ/);
    assert.match(cards.innerHTML, /集計から除外[\s\S]*EURTEST · &lt;img src=x&gt;/);
    assert.doesNotMatch(cards.innerHTML, /<img|\sstyle=/);
    assertAccessibleControls(markup() + cards.innerHTML);
    assert.equal(byId('watchlist-cash-input').value, '2,500');

    globalThis.fetch = async () => response({ items: [], cash_jpy: 0, portfolio: null });
    await selectWatchlist(9);
    assert.equal(cards.classList.contains('hidden'), true);
    assert.equal(cards.innerHTML, '');
});

test('share and cash inputs debounce, validate, save, clear and announce errors inline', async t => {
    reset();
    const originalTimeout = globalThis.setTimeout;
    const originalClear = globalThis.clearTimeout;
    const timers = new Map();
    let id = 0;
    globalThis.setTimeout = (fn, delay) => { timers.set(++id, { fn, delay }); return id; };
    globalThis.clearTimeout = timer => timers.delete(timer);
    t.after(() => { globalThis.setTimeout = originalTimeout; globalThis.clearTimeout = originalClear; });
    const flush = async () => {
        for (const [key, { fn, delay }] of timers) {
            assert.equal(delay, 250);
            timers.delete(key);
            fn();
        }
        await tick();
    };
    const writes = [];
    let shares = null;
    let cash = 1234567;
    globalThis.fetch = async (path, options) => {
        if (path === summary(8)) return response({ items: [{ symbol: '^N225', shares }], cash_jpy: cash, portfolio: null });
        assert.equal(options.method, 'PUT');
        const body = JSON.parse(options.body);
        writes.push([path, body]);
        if (body.shares === 9) return response({ detail: '株数を保存できませんでした' }, 409);
        if ('shares' in body) shares = body.shares;
        else cash = body.cash_jpy;
        return response(body);
    };
    await selectWatchlist(8);
    const field = byId('shares-editor-test');
    field.dataset.sharesInput = '^N225';
    byId('comparison-tbody').appendChild(field);
    byId('comparison-tbody').querySelectorAll = () => [field];
    const cashField = byId('watchlist-cash-input');
    assert.equal(cashField.value, '1,234,567');
    cashField.dispatchEvent(new Event('focusin'));
    assert.equal(cashField.value, '1234567');

    const event = (field, type) => field.dispatchEvent(new Event(type, { bubbles: true }));
    field.value = '1.12345';
    const enter = () => pressKey(byId('comparison-tbody'), field, 'Enter');
    enter();
    assert.match(byId('shares-error-^N225').textContent, /小数第4位/);
    assert.deepEqual(writes, []);

    field.value = '3.1254';
    event(field, 'change');
    event(field, 'focusout');
    assert.equal(timers.size, 1);
    await flush();
    assert.deepEqual(writes, [[`${root}/8/items/%5EN225/shares`, { shares: 3.1254 }]]);
    assert.equal(state.listItems[0].shares, 3.1254);
    assert.equal(byId('shares-error-^N225').textContent, '');

    field.value = '9';
    enter();
    await flush();
    assert.match(byId('shares-error-^N225').textContent, /保存できません/);
    assert.equal(field.getAttribute('aria-invalid'), 'true');
    field.value = '';
    enter();
    await flush();
    assert.deepEqual(writes.at(-1)[1], { shares: null });
    assert.equal(state.listItems[0].shares, null);

    cashField.value = '10.25';
    event(cashField, 'focusout');
    assert.match(byId('watchlist-cash-error').textContent, /整数/);
    cashField.value = '12345';
    event(cashField, 'focusout');
    await flush();
    assert.deepEqual(writes.at(-1), [`${root}/8/cash`, { cash_jpy: 12345 }]);
    assert.equal(cashField.value, '12,345');
    assert.equal(state.listCash, 12345);
    cashField.value = '';
    event(cashField, 'change');
    await flush();
    assert.deepEqual(writes.at(-1)[1], { cash_jpy: null });
    assert.equal(byId('watchlist-cash-error').textContent, '');
});

test('cached rows appear before bounded refreshes, and a settled batch updates server valuations', async () => {
    reset();
    const symbols = ['AAPL', 'MSFT', 'QCOM', 'INTC', 'GOOG', 'META'];
    const snapshots = symbols.map(() => Promise.withResolvers());
    let summaries = 0;
    let requests = 0;
    globalThis.fetch = async path => {
        if (path === summary(8)) {
            summaries++;
            return response({ items: symbols.map(symbol => ({ symbol, pending: summaries === 1,
                name: 'キャッシュ', value_jpy: summaries === 1 ? null : 40 })),
                portfolio: summaries === 1 ? null : portfolio });
        }
        const symbol = path.split('/')[2];
        const index = symbols.indexOf(symbol);
        assert.equal(path, `/stock/${symbol}/snapshot`);
        requests++;
        await snapshots[index].promise;
        return response({ symbol, name: `${symbol} 更新済み`, price: 20 });
    };
    await selectWatchlist(8);
    assert.equal(summaries, 1);
    assert.equal(requests, 4);
    assert.match(markup(), /キャッシュ[\s\S]*>…<\/td>/);
    assert.doesNotMatch(markup(), /更新済み/);
    assert.equal(byId('watchlist-refresh').textContent, '更新中…');
    snapshots[0].resolve();
    await tick();
    assert.equal(requests, 5);
    assert.equal(summaries, 1);
    assert.match(markup(), /AAPL 更新済み/);
    assert.doesNotMatch(markup(), /MSFT 更新済み/);
    snapshots.forEach(s => s.resolve());
    await tick();
    assert.equal(summaries, 2);
    assert.match(markup(), /MSFT 更新済み/);
    assert.match(markup(), />40<\/td>/);
    assert.match(byId('watchlist-portfolio').innerHTML, /評価額合計/);
    assert.equal(byId('watchlist-refresh').textContent, '');
});

test('late snapshot or summary responses are ignored after a switch or logout', async () => {
    reset();
    const snap = Promise.withResolvers();
    const late = Promise.withResolvers();
    let snapOptions;
    let start = 0;
    globalThis.fetch = async (path, options) => {
        if (path === summary(8)) {
            start++;
            if (start > 1) return late.promise;
            return response({ items: [{ symbol: 'AAPL', name: '旧データ', stale: true }] });
        }
        if (path === summary(9)) return response({ items: [{ symbol: 'AAPL', name: '別リスト' }] });
        snapOptions = options;
        return snap.promise;
    };
    await selectWatchlist(8);
    await selectWatchlist(9);
    assert.equal(snapOptions.signal.aborted, true);
    snap.resolve(response({ symbol: 'AAPL', name: '遅いレスポンス' }));
    await tick();
    assert.equal(start, 1);
    assert.match(markup(), /別リスト/);
    assert.doesNotMatch(markup(), /遅いレスポンス/);

    const request = selectWatchlist(8);
    state.currentUser = null;
    resetListSession();
    late.resolve(response({ items: [{ symbol: 'AAPL', name: 'ログアウト前のデータ' }] }));
    await request;
    assert.equal(markup(), '');
    assert.equal(byId('watchlist-refresh').textContent, '');
});
