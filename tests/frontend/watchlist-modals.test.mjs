import assert from 'node:assert/strict';
import test from 'node:test';
import { elements, fromRepo, pressKey, storage } from './helpers/dom-shim.mjs';

const { bindListEvents, editStockMemo, renderWatchlistSelector } = await fromRepo('frontend/js/list.js');
const { cache, state } = await fromRepo('frontend/js/state.js');
const byId = id => document.getElementById(id);
const isHidden = id => byId(id).classList.contains('hidden');
const tick = () => new Promise(resolve => setImmediate(resolve));
const reply = (data, status = 200) => ({ ok: status < 400, status, json: async () => data });
const overlay = () => byId('watchlist-action-overlay');
const nameField = () => byId('watchlist-action-name');
const root = '/user/7/lists';
const DEFAULT = { id: 8, name: 'マイリスト' };
const OTHER = { id: 9, name: '別のリスト' };

function reset(other = OTHER) {
    elements.clear();
    cache.clear();
    storage.clear();
    state.currentUser = { id: 7, access_token: 'test' };
    state.currentListId = 9;
    state.defaultListId = 8;
    state.watchlists = [DEFAULT, other];
    state.listItems = [];
    bindListEvents();
    renderWatchlistSelector();
}

function mockLists(mutate) {
    const calls = [];
    globalThis.fetch = async (path, options) => {
        const method = options.method || 'GET';
        const body = options.body ? JSON.parse(options.body) : undefined;
        calls.push([path, method, body]);
        if (method !== 'GET') return mutate(path, method, body);
        if (path === root) return reply({ lists: state.watchlists, default_list_id: DEFAULT.id });
        if (/\/summary$/.test(path)) return reply({ items: [] });
        assert.fail(`Unexpected request: ${path}`);
    };
    return calls;
}

test('create dialog validates the name inline and submits a trimmed name on Enter', async () => {
    reset();
    const calls = mockLists((_path, _method, { name }) => {
        const created = { id: 10, name };
        state.watchlists.push(created);
        return reply(created);
    });
    const create = byId('watchlist-create');
    create.click();
    assert.equal(isHidden('watchlist-action-overlay'), false);
    assert.equal(document.activeElement, nameField());
    assert.match(byId('watchlist-action-title').textContent, /新規作成/);

    for (const invalid of ['　 ', '株'.repeat(51)]) {
        nameField().value = invalid;
        pressKey(overlay(), nameField(), 'Enter');
        await tick();
        assert.match(byId('watchlist-action-error').textContent, /1〜50文字/);
        assert.equal(isHidden('watchlist-action-error'), false);
        assert.deepEqual(calls, []);
    }
    nameField().value = ' 配当株 ';
    pressKey(overlay(), nameField(), 'Enter', { isComposing: true });
    assert.deepEqual(calls, []);
    pressKey(overlay(), nameField(), 'Enter');
    await tick();
    assert.deepEqual(calls, [
        [root, 'POST', { name: '配当株' }], [root, 'GET', undefined], [`${root}/10/summary`, 'GET', undefined],
    ]);
    assert.equal(state.currentListId, 10);
    assert.equal(isHidden('watchlist-action-overlay'), true);
    assert.equal(document.activeElement, create);
});

test('rename dialog prefills the list name, cancels with Escape and displays server errors inline', async () => {
    reset();
    const calls = mockLists((_path, _method, { name }) => {
        if (name === DEFAULT.name) return reply({ detail: '同じ名前のリストが既にあります' }, 409);
        const updated = { ...OTHER, name };
        state.watchlists = [DEFAULT, updated];
        return reply(updated);
    });
    const rename = byId('watchlist-rename');
    rename.click();
    assert.equal(nameField().value, OTHER.name);
    assert.match(byId('watchlist-action-title').textContent, /名前|リスト名/);
    pressKey(overlay(), nameField(), 'Escape');
    assert.equal(isHidden('watchlist-action-overlay'), true);
    assert.equal(document.activeElement, rename);
    assert.deepEqual(calls, []);

    rename.click();
    nameField().value = DEFAULT.name;
    byId('watchlist-action-form').requestSubmit();
    await tick();
    assert.equal(isHidden('watchlist-action-overlay'), false);
    assert.match(byId('watchlist-action-error').textContent, /同じ名前のリスト/);
    nameField().value = '変更後';
    byId('watchlist-action-form').requestSubmit();
    await tick();
    assert.deepEqual(calls.slice(0, 2), [
        [`${root}/9`, 'PUT', { name: DEFAULT.name }], [`${root}/9`, 'PUT', { name: '変更後' }],
    ]);
    assert.equal(isHidden('watchlist-action-overlay'), true);
    assert.match(byId('watchlist-select').innerHTML, /変更後/);
});

test('delete renders an untrusted list name as text and calls the API only after confirmation', async () => {
    const name = '<img src=x onerror=alert(1)>';
    reset({ ...OTHER, name });
    const calls = mockLists(() => {
        state.watchlists = [DEFAULT];
        return reply({ message: 'リストを削除しました' });
    });
    const remove = byId('watchlist-delete');
    remove.click();
    const message = byId('watchlist-action-message');
    assert.equal(message.textContent, `「${name}」を削除しますか？`);
    assert.equal(message.innerHTML, '');
    assert.equal(isHidden('watchlist-action-fields'), true);
    assert.equal(document.activeElement, message);
    byId('watchlist-action-close').click();
    assert.deepEqual(calls, []);
    assert.equal(isHidden('watchlist-action-overlay'), true);

    remove.click();
    pressKey(overlay(), message, 'Enter');
    await tick();
    assert.deepEqual(calls, [
        [`${root}/9`, 'DELETE', undefined], [root, 'GET', undefined], [`${root}/8/summary`, 'GET', undefined],
    ]);
    assert.equal(state.currentListId, DEFAULT.id);
    assert.equal(remove.disabled, true);
    assert.equal(isHidden('watchlist-action-overlay'), true);
    assert.equal(document.activeElement, byId('watchlist-select'));
});

test('memo dialog keeps the existing save API and supports edits, newlines, errors and clearing', async () => {
    reset();
    state.listItems = [{ symbol: 'AAPL', memo: '前のメモ' }];
    const calls = [];
    globalThis.fetch = async (path, options) => {
        const body = JSON.parse(options.body);
        calls.push([path, options.method, body]);
        if (calls.length === 1) return reply({ detail: 'もう一度お試しください' }, 400);
        return reply(body);
    };
    const input = byId('memo-edit-input');
    input.tagName = 'TEXTAREA';
    editStockMemo('AAPL');
    assert.equal(input.value, '前のメモ');
    assert.equal(document.activeElement, input);
    const memoOverlay = byId('memo-edit-overlay');
    assert.equal(pressKey(memoOverlay, input, 'Enter', { shiftKey: true }).defaultPrevented, false);
    assert.deepEqual(calls, []);

    input.value = '新しい行\n次の行';
    pressKey(memoOverlay, input, 'Enter');
    await tick();
    assert.equal(isHidden('memo-edit-overlay'), false);
    assert.match(byId('memo-edit-error').textContent, /もう一度お試しください/);
    pressKey(memoOverlay, input, 'Enter');
    await tick();
    assert.equal(isHidden('memo-edit-overlay'), true);
    assert.equal(state.listItems[0].memo, '新しい行\n次の行');

    editStockMemo('AAPL');
    input.value = '  ';
    pressKey(memoOverlay, input, 'Enter');
    await tick();
    assert.equal(state.listItems[0].memo, null);
    assert.deepEqual(calls, [
        ['/user/7/memo/AAPL', 'PUT', { memo: '新しい行\n次の行' }],
        ['/user/7/memo/AAPL', 'PUT', { memo: '新しい行\n次の行' }],
        ['/user/7/memo/AAPL', 'PUT', { memo: null }],
    ]);
});
