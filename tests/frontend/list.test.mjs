import assert from 'node:assert/strict';
import test from 'node:test';
import { elements, fromRepo, storage } from './helpers/dom-shim.mjs';

const { cache, state } = await fromRepo('frontend/js/state.js');
const { loadDefaultList, openListAddModal, selectWatchlist, submitListAddFromModal } = await fromRepo('frontend/js/list.js');

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
}

test('watchlist loads stock data with one summary request and renders partial and failed rows', async () => {
    resetWatchlist();
    const replies = {
        '/user/7/lists': response({ lists: state.watchlists, default_list_id: 8 }),
        '/user/7/lists/8/summary': response({ items: [
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
    assert.deepEqual(calls, ['/user/7/lists', '/user/7/lists/8/summary']);
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
        '/user/7/lists/8/summary': response({ items: [
            { symbol: '7203.T', name: 'トヨタ', currency: 'JPY', error: '一部のデータを取得できませんでした。' },
        ] }),
    };
    globalThis.fetch = async (path, options) => {
        calls.push([path, options.method]);
        return replies[path];
    };

    openListAddModal('7203.t');
    await submitListAddFromModal();
    assert.deepEqual(calls, [['/user/7/lists/8/items', 'POST'], ['/user/7/lists/8/summary', undefined]]);
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
    assert.deepEqual(calls, ['/user/7/lists', '/user/7/lists/9/summary']);
    assert.equal(document.getElementById('watchlist-delete').disabled, false);
    await selectWatchlist(8);
    assert.deepEqual(calls.slice(2), ['/user/7/lists/8/summary']);
    assert.equal(storage.get('stockAnalyzerWatchlist:7'), '8');
    assert.equal(document.getElementById('watchlist-delete').disabled, true);
});
