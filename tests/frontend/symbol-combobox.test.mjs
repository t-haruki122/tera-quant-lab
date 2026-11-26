import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { elements, fromRepo, pressKey } from './helpers/dom-shim.mjs';

const response = data => ({ ok: true, status: 200, json: async () => data });
globalThis.fetch = async path => {
    assert.equal(path, '/forex/usdjpy');
    return response({ rate: 150 });
};
const { App } = await fromRepo('frontend/app.js');
const { openListAddModal } = await fromRepo('frontend/js/list.js');
const { state, cache } = await fromRepo('frontend/js/state.js');
const { bindSymbolCombobox } = await fromRepo('frontend/js/symbol-combobox.js');
const tick = () => new Promise(resolve => setImmediate(resolve));
await tick();

const ntt = { symbol: '9432.T', name: 'ＮＴＴ', segment: 'プライム' };
const toyota = { symbol: '7203.T', name: 'トヨタ自動車', segment: 'プライム' };
const html = readFileSync('frontend/index.html', 'utf8');
const byId = id => document.getElementById(id);
const change = (input, value, isComposing = false) => {
    input.value = value;
    const event = new Event('input');
    Object.defineProperty(event, 'isComposing', { value: isComposing });
    input.dispatchEvent(event);
};
const key = (input, name, options) => pressKey(input, input, name, options);

function box(inputId = 'input', listId = 'options') {
    const input = byId(inputId), listbox = byId(listId);
    input.setAttribute('aria-controls', listId);
    return { input, listbox };
}

test.beforeEach(() => {
    elements.clear();
    cache.clear();
    state.currentUser = null;
    state.listItems = [];
});

function fakeTimers(t) {
    const pending = new Map();
    let next = 0;
    t.mock.method(globalThis, 'setTimeout', (fn, delay) => {
        assert.equal(delay, 200);
        pending.set(++next, fn);
        return next;
    });
    t.mock.method(globalThis, 'clearTimeout', id => pending.delete(id));
    return {
        pending,
        run() {
            const calls = [...pending.values()];
            pending.clear();
            return calls.map(fn => fn());
        },
    };
}

test('suggestions follow ARIA combobox keyboard behavior and render untrusted labels as text', async t => {
    const timers = fakeTimers(t);
    const { input, listbox } = box();
    const submitted = [];
    const unsafe = '<img src=x onerror=alert(1)>';
    bindSymbolCombobox(input, listbox, {
        onSubmit: () => submitted.push(input.value),
        request: async path => {
            assert.equal(path, '/symbols/search?q=NTT');
            return { items: [ntt, { ...toyota, name: unsafe, segment: unsafe }, { symbol: unsafe }] };
        },
    });
    change(input, 'N');
    change(input, 'NT');
    change(input, 'NTT');
    assert.equal(timers.pending.size, 1);
    await Promise.all(timers.run());
    assert.equal(input.getAttribute('aria-expanded'), 'true');
    assert.equal(input.getAttribute('aria-controls'), 'options');
    assert.equal(input.getAttribute('aria-activedescendant'), null);
    assert.equal(listbox.hidden, false);
    const [first, second] = listbox.children;
    assert.equal(listbox.children.length, 2);
    assert.equal(first.getAttribute('role'), 'option');
    assert.equal(first.textContent, '9432.T ＮＴＴ（プライム）');
    assert.equal(second.textContent, `7203.T ${unsafe}（${unsafe}）`);
    assert.equal(second.innerHTML, '');
    assert.ok(key(input, 'ArrowDown').defaultPrevented);
    assert.equal(input.getAttribute('aria-activedescendant'), first.id);
    assert.equal(first.getAttribute('aria-selected'), 'true');
    key(input, 'ArrowUp');
    assert.equal(input.getAttribute('aria-activedescendant'), second.id);
    assert.equal(first.getAttribute('aria-selected'), 'false');
    assert.equal(second.getAttribute('aria-selected'), 'true');
    assert.ok(key(input, 'Enter').defaultPrevented);
    assert.deepEqual(submitted, ['7203.T']);
    assert.equal(input.getAttribute('aria-expanded'), 'false');
    assert.equal(input.getAttribute('aria-activedescendant'), null);
    assert.equal(listbox.hidden, true);

    change(input, 'NTT');
    await Promise.all(timers.run());
    assert.ok(key(input, 'Escape').defaultPrevented);
    assert.equal(input.getAttribute('aria-expanded'), 'false');
    assert.equal(input.value, 'NTT');
    change(input, 'AAPL');
    key(input, 'Enter');
    assert.deepEqual(submitted, ['7203.T', 'AAPL']);
    assert.equal(timers.pending.size, 0);
});

test('IME pauses suggestions and stale or dismissed responses never reopen the popup', async t => {
    const timers = fakeTimers(t);
    const { input, listbox } = box();
    const old = Promise.withResolvers();
    const current = Promise.withResolvers();
    const dismissed = Promise.withResolvers();
    const calls = [];
    bindSymbolCombobox(input, listbox, {
        onSubmit: () => assert.fail('IME Enter submitted'),
        request: path => {
            const query = new URL(path, 'http://test').searchParams.get('q');
            calls.push(query);
            return { NTT: old.promise, トヨタ: current.promise, ソニー: dismissed.promise }[query];
        },
    });
    change(input, 'NTT');
    const [oldRequest] = timers.run();
    input.dispatchEvent(new Event('compositionstart'));
    change(input, 'と', true);
    change(input, 'とよ', false);
    key(input, 'Enter', { isComposing: true });
    key(input, 'Enter', { keyCode: 229 });
    assert.equal(timers.pending.size, 0);
    input.value = 'トヨタ';
    input.dispatchEvent(new Event('compositionend'));
    const [newRequest] = timers.run();
    assert.deepEqual(calls, ['NTT', 'トヨタ']);
    current.resolve({ items: [toyota] });
    await newRequest;
    old.resolve({ items: [ntt] });
    await oldRequest;
    assert.equal(listbox.children[0].textContent, '7203.T トヨタ自動車（プライム）');
    change(input, 'ソニー');
    const [lastRequest] = timers.run();
    key(input, 'Escape');
    dismissed.resolve({ items: [{ symbol: '6758.T', name: 'ソニー' }] });
    await lastRequest;
    assert.equal(listbox.hidden, true);
});

test('clicking dashboard and header suggestions adds a watchlist row and opens stock detail', async t => {
    const timers = fakeTimers(t);
    const dashboard = box('list-add-modal-input', 'list-add-symbol-options');
    const header = box('search-input', 'search-symbol-options');
    globalThis.fetch = async path => {
        assert.match(path, /^\/stock\/9432\.T(?:\/|$)/);
        return response({ symbol: '9432.T', price: 100, name: 'ＮＴＴ' });
    };
    const navigations = [];
    t.mock.method(window, 'switchView', (...args) => navigations.push(args));
    new App({ fetchAPI: async path => ({ items: path.endsWith('NTT') ? [ntt] : [toyota] }) }).bindCoreEvents();
    openListAddModal();
    change(dashboard.input, 'NTT');
    await Promise.all(timers.run());
    dashboard.listbox.children[0].click();
    await tick();
    assert.deepEqual(state.listItems.map(item => item.symbol), ['9432.T']);
    assert.equal(byId('list-add-modal-overlay').classList.contains('hidden'), true);
    change(header.input, 'トヨタ');
    await Promise.all(timers.run());
    header.listbox.children[0].click();
    assert.deepEqual(navigations, [['detail', '7203.T']]);
});

test('all symbol inputs declare their name, popup relationship and initial state', () => {
    for (const [inputId, listId] of [
        ['search-input', 'search-symbol-options'],
        ['list-add-modal-input', 'list-add-symbol-options'],
        ['backtest-symbol', 'backtest-symbol-options'],
    ]) {
        assert.match(html, new RegExp(`<label[^>]+for="${inputId}"`));
        assert.match(html, new RegExp(`<input[^>]+id="${inputId}"[^>]+role="combobox"[^>]+aria-expanded="false"[^>]+aria-controls="${listId}"`));
        assert.match(html, new RegExp(`id="${listId}" role="listbox" aria-label="[^"]+" hidden`));
    }
});
