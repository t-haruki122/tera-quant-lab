import { state } from './state.js';
import { fetchAPI } from './api.js';
import { openAuthModal } from './auth.js';
import { getDetailQuote } from './detail.js';
import { normalizeSymbol, serializeRoute } from './router.js';
import { createDialog } from './dialog.js';
import { createActionModal } from './action-modal.js';
import { alertKinds, bindNotificationButton } from './alerts.js';
import { escapeHtml, formatTimestamp, showToast } from './utils.js';

const byId = id => document.getElementById(id);
const listDialog = createDialog('alerts-list-overlay');
const alertModal = createActionModal('alert-editor');
const deleteModal = createActionModal('alert-delete');
let visible = false;
let alertRequest = 0;
let alerts = [];
let refreshNotification;
const number = (value, digits = 6) => value == null ? '—'
    : value.toLocaleString('ja-JP', { maximumFractionDigits: digits });
const percent = value => value == null ? '—' : `${number(value, 2)}%`;
const path = (user, section) => `/user/${encodeURIComponent(user.id)}` + '/' + section;

function feedback(id, message = '') {
    const element = byId(id);
    element.textContent = message;
    element.classList.toggle('hidden', !message);
}

function symbolLink(symbol) {
    return `<a href="${escapeHtml(serializeRoute({ view: 'detail', symbol }))}">${escapeHtml(symbol)}</a>`;
}

function rowActions(row) {
    return `<div class="alert-actions">
        <button type="button" class="account-action" data-edit-alert="${escapeHtml(row.id)}"
            aria-label="${escapeHtml(`${row.symbol} の価格アラートを編集`)}">編集</button>
        <button type="button" class="account-action account-action-danger" data-delete-alert="${escapeHtml(row.id)}"
            aria-label="${escapeHtml(`${row.symbol} の価格アラートを削除`)}">削除</button></div>`;
}

function replaceRows(id, html, fallback) {
    const table = byId(id);
    const hadFocus = table.contains(document.activeElement);
    table.innerHTML = html;
    if (hadFocus) byId(fallback).focus();
}

function alertHistory(alert) {
    return alert.last_triggered_at ? `${formatTimestamp(alert.last_triggered_at)}（累計 ${alert.trigger_count} 回）` : '—';
}

function renderAlerts() {
    const html = alerts.map(alert => `<tr>
        <th scope="row">${symbolLink(alert.symbol)}${alert.note ? `<small>${escapeHtml(alert.note)}</small>` : ''}</th>
        <td>${escapeHtml(alertKinds[alert.kind])} ${escapeHtml(alert.kind === 'change_pct' ? percent(alert.threshold) : number(alert.threshold))}</td>
        <td>${alert.enabled ? '有効' : '停止中'}</td>
        <td data-alert-history="${escapeHtml(alert.id)}">${escapeHtml(alertHistory(alert))}</td>
        <td>${rowActions(alert)}</td></tr>`).join('');
    replaceRows('alerts-list-rows', html, 'alerts-list-add');
}

async function loadAlerts(user) {
    const requestId = ++alertRequest;
    feedback('alerts-list-error');
    byId('alerts-list-status').textContent = '価格アラートを取得中…';
    try {
        const data = await fetchAPI(path(user, 'alerts'), { cache: 'no-store' });
        if (alertRequest !== requestId || user !== state.currentUser) return;
        alerts = data.alerts;
        renderAlerts();
        byId('alerts-list-status').textContent = alerts.length ? '' : '価格アラートはまだありません。';
    } catch (error) {
        if (alertRequest === requestId && user === state.currentUser) {
            byId('alerts-list-status').textContent = '';
            feedback('alerts-list-error', error.message);
        }
    }
}

export function showAlertCheckResult({ fired, skipped }) {
    if (!visible) return;
    for (const hit of fired) {
        const alert = alerts.find(item => item.id === hit.id);
        if (!alert) continue;
        Object.assign(alert, { last_triggered_at: hit.last_triggered_at, trigger_count: hit.trigger_count });
        const cell = [...byId('alerts-list-rows').querySelectorAll('[data-alert-history]')]
            .find(cell => cell.dataset.alertHistory === String(hit.id));
        if (cell) cell.textContent = alertHistory(alert);
    }
    if (alerts.length) byId('alerts-list-status').textContent = skipped
        .map(item => `${item.symbol}：${item.reason}`).join(' ');
}

function closeList() {
    if (!visible) return;
    visible = false;
    ++alertRequest;
    alerts = [];
    listDialog.close();
    renderAlerts();
    byId('alerts-list-status').textContent = '';
    feedback('alerts-list-error');
}

export function closeAlerts() {
    alertModal.close();
    deleteModal.close();
    closeList();
}

function requireUser(opener) {
    if (!state.currentUser) openAuthModal('login', opener);
    return state.currentUser;
}

function readNumber(id, message) {
    const input = byId(id);
    const value = Number(input.value);
    if (!input.value.trim() || !Number.isFinite(value) || value <= 0) {
        throw new Error(message);
    }
    return value;
}

function readSymbol(id) {
    const symbol = normalizeSymbol(byId(id).value);
    if (!symbol) throw new Error('有効なティッカーを入力してください。');
    return symbol;
}

function readNote(id) {
    const note = byId(id).value.trim();
    if ([...note].length > 2000) throw new Error('メモは2000文字以内にしてください。');
    return note || null;
}

function save(user, section, id, data) {
    if (user !== state.currentUser) throw new Error('ログイン状態が変更されました。');
    return fetchAPI(path(user, id ? `${section}/${encodeURIComponent(id)}` : section), {
        method: id ? 'PUT' : 'POST', body: JSON.stringify(data),
    });
}

function updateThresholdLabel() {
    byId('alert-threshold-label').textContent = byId('alert-kind').value === 'change_pct'
        ? '基準騰落率（%、絶対値）' : '基準価格（銘柄の通貨）';
}

function editAlert(opener, alert = {}) {
    const user = requireUser(opener);
    if (!user) return;
    byId('alert-editor-title').textContent = alert.id ? '価格アラートを編集' : '価格アラートを追加';
    byId('alert-symbol').value = alert.symbol ?? '';
    byId('alert-kind').value = alert.kind ?? 'above';
    byId('alert-threshold').value = String(alert.threshold ?? '');
    byId('alert-enabled').checked = alert.enabled ?? true;
    byId('alert-note').value = alert.note ?? '';
    updateThresholdLabel();
    alertModal.open({
        focus: alert.symbol ? byId('alert-kind') : byId('alert-symbol'), opener, fallbackFocus: byId('alerts-list-add'),
        async onSubmit() {
            await save(user, 'alerts', alert.id, {
                symbol: readSymbol('alert-symbol'), kind: byId('alert-kind').value,
                threshold: readNumber('alert-threshold', '基準値は0より大きい数値を入力してください。'),
                enabled: byId('alert-enabled').checked, note: readNote('alert-note'),
            });
            showToast(alert.id ? '価格アラートを更新しました。' : '価格アラートを追加しました。', 'success');
            if (visible) void loadAlerts(user);
        },
    });
}

function confirmDelete(opener, item) {
    const user = state.currentUser;
    if (!user) return;
    const message = byId('alert-delete-message');
    message.textContent = `${item.symbol} の価格アラートを削除しますか？`;
    deleteModal.open({
        focus: message, opener, fallbackFocus: byId('alerts-list-add'),
        async onSubmit() {
            if (user !== state.currentUser) throw new Error('ログイン状態が変更されました。');
            await fetchAPI(path(user, `alerts/${encodeURIComponent(item.id)}`), { method: 'DELETE' });
            showToast('価格アラートを削除しました。', 'success');
            if (visible) void loadAlerts(user);
        },
    });
}

export function bindAlertDialogEvents() {
    alertModal.bind();
    deleteModal.bind();
    listDialog.bind(closeList);
    byId('alerts-list-close').addEventListener('click', closeList);
    refreshNotification = bindNotificationButton(byId('alert-notification-enable'), byId('alert-notification-status'));
    byId('watchlist-alerts').addEventListener('click', event => {
        const opener = event.currentTarget;
        const user = requireUser(opener);
        if (!user) return;
        visible = true;
        renderAlerts();
        listDialog.open(byId('alerts-list-close'), { opener });
        refreshNotification();
        void loadAlerts(user);
    });
    byId('alerts-list-add').addEventListener('click', event => editAlert(event.currentTarget));
    byId('btn-detail-alert').addEventListener('click', event => {
        if (state.currentSymbol) editAlert(event.currentTarget, {
            symbol: state.currentSymbol, threshold: getDetailQuote()?.price,
        });
    });
    byId('alert-kind').addEventListener('change', updateThresholdLabel);
    byId('alerts-list-rows').addEventListener('click', event => {
        const button = event.target.closest('button');
        if (!button) return;
        const editing = button.getAttribute('data-edit-alert');
        const deleting = button.getAttribute('data-delete-alert');
        const item = alerts.find(row => String(row.id) === (editing ?? deleting));
        if (!item) return;
        if (editing) editAlert(button, item);
        else confirmDelete(button, item);
    });
}
