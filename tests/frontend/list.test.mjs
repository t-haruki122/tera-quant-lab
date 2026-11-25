import assert from 'node:assert/strict';
import test from 'node:test';
import { elements, fromRepo } from './helpers/dom-shim.mjs';

const { cache, state } = await fromRepo('frontend/js/state.js');
const { loadDefaultList, openListAddModal, submitListAddFromModal } = await fromRepo('frontend/js/list.js');

const response = (data, status = 200) => ({ ok: status < 400, status, json: async () => data });
const rateLimited = () => response({ detail: 'Too Many Requests' }, 429);
const markup = () => document.getElementById('comparison-tbody').innerHTML;
const rateLimitMessage = /アクセスが集中しています。しばらくしてから再試行してください。/;

function resetWatchlist() {
    cache.clear();
    elements.clear();
    state.currentUser = { id: 7, access_token: 'test' };
    state.currentListId = 8;
    state.listItems = [];
}

test('watchlist keeps complete and partial rows when a stock request returns 429', async () => {
    resetWatchlist();
    const replies = {
        '/user/7/default-list': response({ id: 8, items: [{ symbol: '7203.T', tags: ['自動車'] }, { symbol: 'AAPL' }] }),
        '/stock/7203.T': response({ price: 2450 }),
        '/stock/7203.T/profile': rateLimited(),
        '/stock/7203.T/indicators': response({ mix_index: 8.1 }),
        '/user/7/memo/7203.T': response({ memo: 'メモを保持' }),
        '/stock/AAPL': response({ price: 250 }),
        '/stock/AAPL/profile': response({ name: 'Apple', currency: 'USD' }),
        '/stock/AAPL/indicators': response({ mix_index: 12 }),
        '/user/7/memo/AAPL': response({ memo: null }),
    };
    const calls = [];
    globalThis.fetch = async path => {
        calls.push(path);
        return replies[path];
    };

    await loadDefaultList();
    assert.equal(calls.length, Object.keys(replies).length);
    assert.ok(state.listItems.every(item => !item.loading));
    const [partial, complete] = state.listItems;
    assert.equal(partial.price, 2450);
    assert.equal(partial.mix_index, 8.1);
    assert.equal(partial.memo, 'メモを保持');
    assert.deepEqual(partial.tags, ['自動車']);
    assert.equal(complete.name, 'Apple');
    assert.equal(complete.loadError, null);
    assert.match(markup(), rateLimitMessage);
    assert.match(markup(), />2,450<\/td>/);
    assert.match(markup(), /Apple/);
});

test('adding a rate-limited symbol keeps its partial row and saved list item', async () => {
    resetWatchlist();
    const calls = [];
    const replies = {
        '/user/7/lists/8/items': response({ symbol: '7203.T' }),
        '/stock/7203.T': rateLimited(),
        '/stock/7203.T/profile': response({ name: 'トヨタ', currency: 'JPY' }),
        '/stock/7203.T/indicators': rateLimited(),
        '/user/7/memo/7203.T': response({ memo: null }),
    };
    globalThis.fetch = async (path, options) => {
        calls.push([path, options.method]);
        return replies[path];
    };

    openListAddModal('7203.t');
    await submitListAddFromModal();
    assert.equal(calls.length, Object.keys(replies).length);
    assert.deepEqual(calls[0], ['/user/7/lists/8/items', 'POST']);
    assert.ok(calls.every(([, method]) => method !== 'DELETE'));
    assert.equal(state.listItems.length, 1);
    assert.equal(state.listItems[0].loading, false);
    assert.match(markup(), /トヨタ/);
    assert.match(markup(), rateLimitMessage);
});
