import assert from 'node:assert/strict';
import test from 'node:test';
import { assertAccessibleControls } from './helpers/a11y.mjs';
import { fromRepo, pressKey } from './helpers/dom-shim.mjs';

const { state } = await fromRepo('frontend/js/state.js');
const { bindAlertDialogEvents, closeAlerts } = await fromRepo('frontend/js/alert-dialog.js');
const { renderStockHeader } = await fromRepo('frontend/js/detail.js');
const byId = id => document.getElementById(id);
const tick = () => new Promise(resolve => setImmediate(resolve));
const reply = data => ({ ok: true, status: 200, json: async () => data });
bindAlertDialogEvents();

test('dashboard alerts open from the toolbar and support editing and deleting with focus restoration', async () => {
    state.currentUser = { id: 7, access_token: 'test' };
    const calls = [];
    let alerts = [{ id: 1, symbol: 'AAPL', kind: 'above', threshold: 120, enabled: true, note: '<tag>' }];
    globalThis.fetch = async (path, options) => {
        calls.push([path, options.method ?? 'GET', options.body && JSON.parse(options.body)]);
        if (options.method === 'PUT') alerts = [{ ...alerts[0], ...JSON.parse(options.body) }];
        if (options.method === 'DELETE') alerts = [];
        return reply({ alerts });
    };
    const opener = byId('watchlist-alerts');
    const overlay = byId('alerts-list-overlay');
    opener.click();
    assert.equal(overlay.classList.contains('hidden'), false);
    assert.equal(document.activeElement, byId('alerts-list-close'));
    await tick();
    const rows = byId('alerts-list-rows');
    assert.match(rows.innerHTML, /AAPL[\s\S]*&lt;tag&gt;/);
    assertAccessibleControls(rows.innerHTML);
    const button = (id, action) => {
        const element = byId(id);
        element.tagName = 'BUTTON';
        element.setAttribute(action, '1');
        rows.appendChild(element);
        return element;
    };
    button('test-edit', 'data-edit-alert').click();
    assert.equal(byId('alert-kind').value, 'above');
    assert.equal(byId('alert-threshold').value, '120');
    byId('alert-threshold').value = '140';
    byId('alert-editor-form').requestSubmit();
    await tick();
    assert.match(rows.innerHTML, /140/);

    button('test-delete', 'data-delete-alert').click();
    assert.match(byId('alert-delete-message').textContent, /AAPL/);
    byId('alert-delete-form').requestSubmit();
    await tick();
    assert.match(byId('alerts-list-status').textContent, /まだありません/);
    assert.deepEqual(calls.map(call => call.slice(0, 2)), [
        ['/user/7/alerts', 'GET'], ['/user/7/alerts/1', 'PUT'], ['/user/7/alerts', 'GET'],
        ['/user/7/alerts/1', 'DELETE'], ['/user/7/alerts', 'GET'],
    ]);
    pressKey(overlay, document.activeElement, 'Escape');
    assert.equal(overlay.classList.contains('hidden'), true);
    assert.equal(document.activeElement, opener);
});

test('detail alert button keeps the ticker and quote as the default threshold', () => {
    state.currentSymbol = 'AAPL';
    renderStockHeader({ symbol: 'AAPL', price: 123.4, timestamp: '2026-11-25T08:00:00Z' }, { currency: 'USD' });
    byId('btn-detail-alert').click();
    assert.equal(byId('alert-editor-overlay').classList.contains('hidden'), false);
    assert.equal(byId('alert-symbol').value, 'AAPL');
    assert.equal(byId('alert-threshold').value, '123.4');
    closeAlerts();
    assert.equal(byId('alert-editor-overlay').classList.contains('hidden'), true);
});
