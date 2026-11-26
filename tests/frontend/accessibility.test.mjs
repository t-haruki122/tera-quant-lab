import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { assertAccessibleControls } from './helpers/a11y.mjs';
import { fromRepo, pressKey } from './helpers/dom-shim.mjs';

const { bindAuthEvents } = await fromRepo('frontend/js/auth.js');
const { bindListEvents, renderListTable } =
    await fromRepo('frontend/js/list.js');
const { loadFinancials } = await fromRepo('frontend/js/detail.js');
const { renderStrategies, renderGridResult } = await fromRepo('frontend/js/backtest.js');
const { state } = await fromRepo('frontend/js/state.js');
const { showToast } = await fromRepo('frontend/js/utils.js');
const byId = id => document.getElementById(id);
const html = readFileSync('frontend/index.html', 'utf8');

test('every static button and field has an accessible name, including icon-only buttons', () => {
    assertAccessibleControls(html);
    assert.throws(() => assertAccessibleControls('<button>✕</button>'), /Button without an accessible name/);
    assert.throws(() => assertAccessibleControls('<input id="sample" placeholder="銘柄">'), /Input without a label/);
    assert.throws(() => assertAccessibleControls('<button aria-labelledby="missing"><svg></svg></button>'), /Button without/);
    assertAccessibleControls('<label for="x">銘柄</label><input id="x"><label>期間<select></select></label>');
    assert.equal((html.match(/class="modal(?: [^"]*)?"/g) ?? []).length,
        (html.match(/role="dialog" aria-modal="true" aria-labelledby="[^"]+"/g) ?? []).length);
});

test('generated watchlist, financial, and backtest controls also have names and labels', async () => {
    state.currentUser = { id: 7 };
    state.listItems = [{ symbol: 'AAPL', name: 'Apple', tags: ['テック'] }, { symbol: '7203.T', loading: true }];
    renderListTable();
    assertAccessibleControls(byId('comparison-tbody').innerHTML);
    assert.match(byId('comparison-tbody').innerHTML, /<a href="#\/stock\/AAPL">AAPL<\/a>/);
    assert.match(byId('comparison-tbody').innerHTML, /for="shares-AAPL">AAPL の株数<\/label>/);

    globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ history: [] }) });
    await loadFinancials('AAPL', {});
    assertAccessibleControls(byId('financials-body').innerHTML);
    renderStrategies({ rank_metrics: ['sharpe'], strategies: [{ name: 'sma', params: [
        { name: '期間', type: 'int', default: 5, min: 1 },
        { name: '方向', type: 'bool', default: true },
    ] }] });
    assertAccessibleControls(byId('backtest-params').innerHTML + byId('backtest-grid-params').innerHTML);
    renderGridResult({ rank_by: 'sharpe', results: [{ rank: 1, params: {}, metrics: {} }] });
    assertAccessibleControls(byId('backtest-grid-table').innerHTML);
});

test('add-stock modal traps Tab in both directions and Escape restores focus', () => {
    bindListEvents();
    const trigger = byId('list-add-btn');
    const overlay = byId('list-add-modal-overlay');
    const close = byId('list-add-modal-close');
    const input = byId('list-add-modal-input');
    const submit = byId('list-add-modal-submit');
    overlay.querySelectorAll = () => [close, input, submit];
    for (const field of [close, input, submit]) overlay.appendChild(field);
    trigger.focus();
    trigger.click();
    assert.equal(document.activeElement, input);
    assert.equal(overlay.classList.contains('hidden'), false);
    submit.focus();
    assert.ok(pressKey(overlay, submit, 'Tab').defaultPrevented);
    assert.equal(document.activeElement, close);
    assert.ok(pressKey(overlay, close, 'Tab', { shiftKey: true }).defaultPrevented);
    assert.equal(document.activeElement, submit);

    assert.ok(pressKey(overlay, submit, 'Escape').defaultPrevented);
    assert.equal(overlay.classList.contains('hidden'), true);
    assert.equal(document.activeElement, trigger);
});

test('user menu opens with native button keys and supports arrows and Escape', () => {
    bindAuthEvents();
    const trigger = byId('account-btn');
    const menu = byId('account-menu');
    const items = [byId('guest-login'), byId('guest-register'), byId('account-menu-stats')];
    const hidden = byId('account-menu-logout');
    hidden.getClientRects = () => [];
    menu.querySelectorAll = () => [...items, hidden];
    assert.match(html, /<button[^>]*id="account-btn"[^>]*aria-haspopup="menu"/);
    trigger.focus();
    for (const key of ['Enter', ' ']) {
        // The shim leaves the native activation default of a button to the test.
        const event = pressKey(trigger, trigger, key);
        if (!event.defaultPrevented) trigger.click();
        assert.equal(trigger.getAttribute('aria-expanded'), 'true');
        assert.equal(document.activeElement, items[0]);
        assert.ok(pressKey(menu, items[0], 'ArrowDown').defaultPrevented);
        assert.equal(document.activeElement, items[1]);
        pressKey(menu, items[1], 'End');
        assert.equal(document.activeElement, items[2]);
        pressKey(menu, items[2], 'ArrowDown');
        assert.equal(document.activeElement, items[0]);
        assert.ok(pressKey(menu, items[0], 'Escape').defaultPrevented);
        assert.equal(trigger.getAttribute('aria-expanded'), 'false');
        assert.equal(document.activeElement, trigger);
    }
    pressKey(trigger, trigger, 'ArrowUp');
    assert.equal(document.activeElement, items[2]);
    pressKey(menu, items[2], 'ArrowUp');
    assert.equal(document.activeElement, items[1]);
    pressKey(menu, items[1], 'Escape');
});

test('activating a sortable header moves and updates aria-sort', () => {
    const symbol = byId('symbol-header');
    const price = byId('price-header');
    symbol.dataset.sort = 'symbol';
    price.dataset.sort = 'price';
    const buttons = [byId('symbol-sort-btn'), byId('price-sort-btn')];
    symbol.appendChild(buttons[0]);
    price.appendChild(buttons[1]);
    assert.match(html, /data-sort="price"><button type="button">株価/);
    const originalQuery = document.querySelectorAll;
    document.querySelectorAll = selector => selector.startsWith('#comparison-table th.sortable') ? [symbol, price] : [];
    try {
        state.sortKey = 'symbol';
        state.sortAsc = true;
        bindListEvents();
        renderListTable();
        assert.equal(symbol.getAttribute('aria-sort'), 'ascending');
        buttons[1].click();
        assert.equal(symbol.getAttribute('aria-sort'), null);
        assert.equal(price.getAttribute('aria-sort'), 'ascending');
        buttons[1].click();
        assert.equal(price.getAttribute('aria-sort'), 'descending');
    } finally {
        document.querySelectorAll = originalQuery;
    }
});

test('toasts announce messages politely and report errors as alerts', () => {
    showToast('読み込み完了', 'success');
    showToast('取得に失敗しました', 'error');
    const [status, error] = byId('toast-container').children;
    assert.equal(status.getAttribute('role'), 'status');
    assert.equal(status.getAttribute('aria-live'), 'polite');
    assert.equal(error.getAttribute('role'), 'alert');
});
