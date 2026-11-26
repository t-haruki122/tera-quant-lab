import assert from 'node:assert/strict';
import test from 'node:test';
import { elements, fromRepo, storage } from './helpers/dom-shim.mjs';

const { cache, state } = await fromRepo('frontend/js/state.js');
const { getMixIndexRating, renderIndicators } = await fromRepo('frontend/js/detail.js');
const { loadDefaultList, openListAddModal, renderListTable, selectWatchlist, submitListAddFromModal } = await fromRepo('frontend/js/list.js');

const response = (data, status = 200) => ({ ok: status < 400, status, json: async () => data });
const rateLimited = () => response({ detail: 'Too Many Requests' }, 429);
const markup = () => document.getElementById('comparison-tbody').innerHTML;
const rateLimitMessage = /アクセスが集中しています。しばらくしてから再試行してください。/;

function resetWatchlist() {
    cache.clear();
    elements.clear();
    storage.clear();
    state.currentUser = { id: 7, access_token: 'test' };
    state.currentListId = 8;
    state.watchlists = [{ id: 8, name: 'リスト' }];
    state.defaultListId = 8;
    state.listItems = [];
    state.sortKey = 'symbol';
    state.sortAsc = true;
}

test('mix index ratings apply only to positive values and keep the existing thresholds', () => {
    for (const value of [undefined, null, -3257.86, 0]) assert.equal(getMixIndexRating(value), null);
    assert.equal(getMixIndexRating(22.49), 'good');
    assert.equal(getMixIndexRating(22.5), 'neutral');
    assert.equal(getMixIndexRating(50), 'bad');
});

test('watchlist shows a dash and escaped text for a negative PBR and keeps null indexes last', () => {
    resetWatchlist();
    state.listItems = [
        { symbol: 'HPQ', name: '<img src=x>', per: 10.67, pbr: -305.39, mix_index: null },
        { symbol: 'HIGH', mix_index: 30 },
        { symbol: 'LOW', mix_index: 10 },
    ];
    state.sortKey = 'mix_index';
    const sortedSymbols = () => [...markup().matchAll(/<tr data-symbol="([^"]+)"/g)].map(match => match[1]);
    renderListTable();
    const row = markup().match(/<tr data-symbol="HPQ">[\s\S]*?<\/tr>/)[0];
    assert.match(row, /<td class="numeric">— <small class="text-muted">債務超過<\/small><\/td>/);
    assert.match(row, /&lt;img src=x&gt;/);
    assert.doesNotMatch(row, /<img|indicator-badge|[◎○△]/);
    assert.deepEqual(sortedSymbols(), ['LOW', 'HIGH', 'HPQ']);
    state.sortAsc = false;
    renderListTable();
    assert.deepEqual(sortedSymbols(), ['HIGH', 'LOW', 'HPQ']);

    state.listItems = [{ symbol: 'LOSS', per: -10, pbr: 2, mix_index: -20 }];
    renderListTable();
    assert.match(markup(), /<td class="numeric">— <small class="text-muted">赤字<\/small><\/td>/);
    assert.doesNotMatch(markup(), /indicator-badge/);
});

test('detail shows text instead of badges for nonpositive PBR and PER', () => {
    for (const value of [-2, 0]) {
        renderIndicators({ pbr: value, per: value, mix_index: null });
        const html = document.getElementById('indicators-body').innerHTML;
        assert.ok(html.includes(`${value}倍 <small class="text-muted">債務超過</small>`));
        assert.ok(html.includes(`${value}倍 <small class="text-muted">赤字</small>`));
        assert.match(html, /title="PBRまたはPERが0以下のため算出できません"/);
        assert.match(html, /— <small class="text-muted">算出不可<\/small>/);
        assert.doesNotMatch(html, /indicator-badge|[◎○△]/);
    }
});

test('watchlist loads stock data with one summary request and renders partial and failed rows', async () => {
    resetWatchlist();
    const replies = {
        '/user/7/lists': response({ lists: state.watchlists, default_list_id: 8 }),
        '/user/7/lists/8/summary?cached=true': response({ items: [
            { symbol: '7203.T', tags: ['自動車'], price: 2450, mix_index: 8.1, memo: 'メモを保持',
              error: '一部のデータを取得できませんでした。' },
            { symbol: 'AAPL', price: 250, name: 'Apple', currency: 'USD', mix_index: 12 },
            { symbol: 'BAD', error: '銘柄データを取得できませんでした。' },
        ] }),
    };
    const calls = [];
    globalThis.fetch = async path => {
        calls.push(path);
        return replies[path];
    };

    await loadDefaultList();
    assert.deepEqual(calls, ['/user/7/lists', '/user/7/lists/8/summary?cached=true']);
    assert.ok(state.listItems.every(item => !item.loading));
    const [partial, complete] = state.listItems;
    assert.equal(partial.price, 2450);
    assert.equal(partial.mix_index, 8.1);
    assert.equal(partial.memo, 'メモを保持');
    assert.deepEqual(partial.tags, ['自動車']);
    assert.equal(complete.name, 'Apple');
    assert.equal(complete.loadError, null);
    assert.match(markup(), /一部のデータを取得できませんでした。/);
    assert.match(markup(), /銘柄データを取得できませんでした。/);
    assert.match(markup(), />2,450<\/td>/);
    assert.match(markup(), /Apple/);
});

test('adding a saved symbol reloads the summary and preserves rows across a rate limit', async () => {
    resetWatchlist();
    const calls = [];
    const replies = {
        '/user/7/lists/8/items': response({ symbol: '7203.T' }),
        '/user/7/lists/8/summary?cached=true': response({ items: [
            { symbol: '7203.T', name: 'トヨタ', currency: 'JPY', error: '一部のデータを取得できませんでした。' },
        ] }),
    };
    globalThis.fetch = async (path, options) => {
        calls.push([path, options.method]);
        return replies[path];
    };

    openListAddModal('7203.t');
    await submitListAddFromModal();
    assert.deepEqual(calls, [['/user/7/lists/8/items', 'POST'], ['/user/7/lists/8/summary?cached=true', undefined]]);
    assert.equal(state.listItems.length, 1);
    assert.ok(!state.listItems[0].loading);
    assert.match(markup(), /トヨタ/);
    globalThis.fetch = async () => rateLimited();
    await selectWatchlist(8);
    assert.match(markup(), /トヨタ/);
    assert.match(document.getElementById('watchlist-error').textContent, rateLimitMessage);
});

test('selecting a saved list makes one request and persists separately per user', async () => {
    resetWatchlist();
    const lists = [{ id: 8, name: 'Default' }, { id: 9, name: 'Second' }];
    const calls = [];
    storage.set('stockAnalyzerWatchlist:7', '9');
    globalThis.fetch = async path => {
        calls.push(path);
        return response(path.endsWith('/lists') ? { lists, default_list_id: 8 } : { items: [] });
    };
    await loadDefaultList();
    assert.equal(state.currentListId, 9);
    assert.deepEqual(calls, ['/user/7/lists', '/user/7/lists/9/summary?cached=true']);
    assert.equal(document.getElementById('watchlist-delete').disabled, false);
    await selectWatchlist(8);
    assert.deepEqual(calls.slice(2), ['/user/7/lists/8/summary?cached=true']);
    assert.equal(storage.get('stockAnalyzerWatchlist:7'), '8');
    assert.equal(document.getElementById('watchlist-delete').disabled, true);
});
