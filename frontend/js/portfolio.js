/** Portfolio valuation, holdings and alert management. */

import { createActionModal } from './action-modal.js';
import { alertKinds, bindNotificationButton } from './alerts.js';
import { downloadAPI, fetchAPI } from './api.js';
import { openAuthModal } from './auth.js';
import { getDetailQuote } from './detail.js';
import { normalizeSymbol, serializeRoute } from './router.js';
import { state } from './state.js';
import { escapeHtml, formatCurrency, formatTimestamp, showToast, staleMessage, staleNotice } from './utils.js';

const byId = id => document.getElementById(id);
const holdingModal = createActionModal('holding-editor');
const alertModal = createActionModal('alert-editor');
const deleteModal = createActionModal('investment-delete');
const compositionPaletteSize = 8;
let activeUser = null;
let visible = false;
let portfolioRequest = 0;
let alertRequest = 0;
let holdings = [];
let alerts = [];
let chart = null;
let sortKey = 'symbol';
let ascending = true;
let refreshNotification;
let duplicate = null;

const number = (value, digits = 6) => value == null ? '—'
    : value.toLocaleString('ja-JP', { maximumFractionDigits: digits });
const percent = value => value == null ? '—' : `${number(value, 2)}%`;
const amount = (value, currency) => currency ? formatCurrency(value, currency, { convertUSD: false }) : number(value);
const jpy = value => amount(value, 'JPY');

function signed(value, render) {
    if (value == null) return '—';
    const formatted = escapeHtml(render(Math.abs(value)));
    if (value === 0) return formatted;
    return value > 0
        ? `<span class="portfolio-gain">▲&nbsp;+${formatted}</span>`
        : `<span class="portfolio-loss">▼&nbsp;−${formatted}</span>`;
}
const path = (user, section) => `/user/${encodeURIComponent(user.id)}` + '/' + section;

function feedback(id, message = '') {
    const element = byId(id);
    element.textContent = message;
    element.classList.toggle('hidden', !message);
}

function symbolLink(symbol) {
    return `<a href="${escapeHtml(serializeRoute({ view: 'detail', symbol }))}">${escapeHtml(symbol)}</a>`;
}

function rowActions(section, row, label) {
    return `<div class="portfolio-actions">
        <button type="button" class="account-action" data-edit-${section}="${escapeHtml(row.id)}"
            aria-label="${escapeHtml(`${row.symbol} の${label}を編集`)}">編集</button>
        <button type="button" class="account-action account-action-danger" data-delete-${section}="${escapeHtml(row.id)}"
            aria-label="${escapeHtml(`${row.symbol} の${label}を削除`)}">削除</button></div>`;
}

function replaceRows(id, html, fallback) {
    const table = byId(id);
    const hadFocus = table.contains(document.activeElement);
    table.innerHTML = html;
    if (hadFocus) byId(fallback).focus();
}

export function renderHoldingTable(rows, key = 'symbol', asc = true) {
    const sorted = [...rows].sort((left, right) => {
        const a = left[key], b = right[key];
        if (a == null || b == null) return Number(a == null) - Number(b == null);
        const order = typeof a === 'number' ? (a - b) : String(a).localeCompare(b, 'ja');
        return asc ? order : -order;
    });
    const html = sorted.map(item => `<tr>
        <th scope="row">${symbolLink(item.symbol)}
            ${item.name ? `<small>${escapeHtml(item.name)}</small>` : ''}
            ${item.acquired_on ? `<small>取得日 ${escapeHtml(item.acquired_on)}</small>` : ''}
            ${item.note ? `<small>${escapeHtml(item.note)}</small>` : ''}
            ${item.error ? `<small class="text-error">${escapeHtml(item.error)}</small>` : ''}
            ${staleNotice(item)}</th>
        <td>${number(item.shares)}</td>
        <td>${escapeHtml(amount(item.avg_cost, item.currency))}</td>
        <td>${escapeHtml(amount(item.price, item.currency))}</td>
        <td>${item.day_change_pct == null
            ? signed(item.day_change, value => amount(value, item.currency))
            : signed(item.day_change_pct, percent)}</td>
        <td>${escapeHtml(item.market_value_jpy == null ? amount(item.market_value, item.currency) : jpy(item.market_value_jpy))}</td>
        <td>${item.unrealized_pl_jpy == null ? signed(item.unrealized_pl, value => amount(value, item.currency))
            : signed(item.unrealized_pl_jpy, jpy)}</td>
        <td>${signed(item.unrealized_pl_pct, percent)}</td>
        <td>${percent(item.weight_pct)}</td>
        <td>${rowActions('holding', item, '保有銘柄')}</td></tr>`).join('');
    replaceRows('portfolio-holdings', html, 'portfolio-add');
    for (const header of document.querySelectorAll('#portfolio-table th[data-sort]')) {
        const selected = header.dataset.sort === key;
        if (selected) header.setAttribute('aria-sort', asc ? 'ascending' : 'descending');
        else header.removeAttribute('aria-sort');
        header.querySelector('span').textContent = selected ? (asc ? '▲' : '▼') : '';
    }
}

export function renderPortfolioTotals(totals) {
    const metrics = [
        ['評価額', escapeHtml(jpy(totals.market_value_jpy))], ['取得額', escapeHtml(jpy(totals.cost_basis_jpy))],
        ['含み損益', signed(totals.unrealized_pl_jpy, jpy)], ['損益率', signed(totals.unrealized_pl_pct, percent)],
    ];
    byId('portfolio-totals').innerHTML = metrics.map(([label, value]) =>
        `<div class="metric-card"><div class="metric-label">${label}</div><div class="metric-value">${value}</div></div>`
    ).join('');
}

export function buildComposition(rows, colors, borderColor) {
    const values = rows.filter(item => Number.isFinite(item.market_value_jpy) && item.market_value_jpy > 0)
        .sort((a, b) => b.market_value_jpy - a.market_value_jpy);
    return {
        labels: values.map(item => item.symbol),
        datasets: [{
            data: values.map(item => item.market_value_jpy),
            backgroundColor: values.map((_, index) => colors[index % colors.length]),
            borderColor,
        }],
    };
}

function renderComposition(rows) {
    chart?.destroy();
    chart = null;
    const canvas = byId('portfolio-chart');
    const legend = byId('portfolio-legend');
    const style = globalThis.getComputedStyle?.(canvas);
    const colors = Array.from({ length: compositionPaletteSize }, (_, index) =>
        style?.getPropertyValue(`--portfolio-segment-${index + 1}`).trim());
    const data = buildComposition(rows, colors, style?.getPropertyValue('--bg-card').trim());
    const { labels, datasets: [{ data: values }] } = data;
    const weights = new Map(rows.map(item => [item.symbol, item.weight_pct]));
    legend.innerHTML = labels.map(symbol => `<li>${escapeHtml(symbol)} ${percent(weights.get(symbol))}</li>`).join('');
    legend.classList.toggle('hidden', !labels.length);
    byId('portfolio-chart-summary').textContent = labels.map((symbol, index) =>
        `${symbol} ${jpy(values[index])}（${percent(weights.get(symbol))}）`
    ).join('\n') || '円換算で評価できる銘柄はありません。';
    const plotted = labels.length > 0 && typeof globalThis.Chart === 'function';
    byId('portfolio-plot').classList.toggle('hidden', !plotted);
    if (plotted) chart = new Chart(canvas, {
        type: 'doughnut', data, options: {
            responsive: true, maintainAspectRatio: false, animation: false, plugins: { legend: { display: false } },
        },
    });
}

async function loadPortfolio(user) {
    const requestId = ++portfolioRequest;
    const button = byId('portfolio-refresh');
    button.disabled = true;
    feedback('portfolio-error');
    byId('portfolio-status').textContent = '評価額を取得中…';
    try {
        const data = await fetchAPI(path(user, 'portfolio'), { cache: 'no-store' });
        if (portfolioRequest !== requestId) return;
        holdings = data.holdings;
        renderHoldingTable(holdings, sortKey, ascending);
        renderPortfolioTotals(data.totals);
        renderComposition(holdings);
        byId('portfolio-status').textContent = [
            holdings.length ? '' : '保有銘柄はまだありません。',
            data.partial ? '一部の銘柄を円換算の合計から除外しています。' : '',
            staleMessage(data),
            data.forex_stale ? `USD/JPY はキャッシュを使用しています（${formatTimestamp(data.forex_cached_at)}）。` : '',
        ].filter(Boolean).join(' ');
    } catch (error) {
        if (portfolioRequest === requestId) {
            byId('portfolio-status').textContent = '';
            feedback('portfolio-error', error.message);
        }
    } finally {
        if (portfolioRequest === requestId) button.disabled = false;
    }
}

function alertHistory(alert) {
    return alert.last_triggered_at ? `${formatTimestamp(alert.last_triggered_at)}（累計 ${alert.trigger_count} 回）` : '—';
}

function renderAlerts() {
    const html = alerts.map(alert => `<tr>
        <th scope="row">${symbolLink(alert.symbol)}${alert.note ? `<small>${escapeHtml(alert.note)}</small>` : ''}</th>
        <td>${escapeHtml(alertKinds[alert.kind])} ${alert.kind === 'change_pct' ? percent(alert.threshold) : number(alert.threshold)}</td>
        <td>${alert.enabled ? '有効' : '停止中'}</td>
        <td data-alert-history="${escapeHtml(alert.id)}">${escapeHtml(alertHistory(alert))}</td>
        <td>${rowActions('alert', alert, 'アラート')}</td></tr>`).join('');
    replaceRows('portfolio-alerts', html, 'portfolio-alert-add');
}

async function loadAlerts(user) {
    const requestId = ++alertRequest;
    feedback('portfolio-alert-error');
    byId('portfolio-alert-status').textContent = '価格アラートを取得中…';
    try {
        const data = await fetchAPI(path(user, 'alerts'), { cache: 'no-store' });
        if (alertRequest !== requestId) return;
        alerts = data.alerts;
        renderAlerts();
        byId('portfolio-alert-status').textContent = alerts.length ? '' : '価格アラートはまだありません。';
    } catch (error) {
        if (alertRequest === requestId) {
            byId('portfolio-alert-status').textContent = '';
            feedback('portfolio-alert-error', error.message);
        }
    }
}

export function showAlertCheckResult({ fired, skipped }) {
    if (!visible) return;
    for (const hit of fired) {
        const alert = alerts.find(item => item.id === hit.id);
        if (!alert) continue;
        Object.assign(alert, { last_triggered_at: hit.last_triggered_at, trigger_count: hit.trigger_count });
        const cell = byId('portfolio-alerts').querySelector(`[data-alert-history="${hit.id}"]`);
        if (cell) cell.textContent = alertHistory(alert);
    }
    if (alerts.length) byId('portfolio-alert-status').textContent = skipped
        .map(item => `${item.symbol}：${item.reason}`).join(' ');
}

export function refreshPortfolioSession(isVisible) {
    visible = isVisible;
    const user = state.currentUser;
    if (user !== activeUser) {
        activeUser = user;
        ++portfolioRequest;
        ++alertRequest;
        holdingModal.close();
        alertModal.close();
        deleteModal.close();
        holdings = [];
        alerts = [];
        renderHoldingTable([]);
        renderPortfolioTotals({});
        renderComposition([]);
        renderAlerts();
        byId('portfolio-status').textContent = '';
        byId('portfolio-alert-status').textContent = '';
        feedback('portfolio-error');
        feedback('portfolio-alert-error');
    }
    if (!visible) return;
    byId('portfolio-login').classList.toggle('hidden', Boolean(user));
    byId('portfolio-content').classList.toggle('hidden', !user);
    if (user) {
        refreshNotification?.();
        return Promise.all([loadPortfolio(user), loadAlerts(user)]);
    }
}

function requireUser(opener) {
    if (!state.currentUser) openAuthModal('login', opener);
    return state.currentUser;
}

function readNumber(id, message, zeroAllowed = false) {
    const input = byId(id);
    const value = Number(input.value);
    if (!input.value.trim() || !Number.isFinite(value) || value < 0 || (!zeroAllowed && value === 0)) {
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

function editHolding(opener, holding = {}) {
    const user = requireUser(opener);
    if (!user) return;
    const existing = byId('holding-edit-existing');
    existing.classList.add('hidden');
    existing.disabled = false;
    duplicate = null;
    byId('holding-editor-title').textContent = holding.id ? '保有銘柄を編集' : '保有銘柄を追加';
    const symbolField = byId('holding-symbol');
    symbolField.value = holding.symbol ?? '';
    symbolField.readOnly = Boolean(holding.id);
    byId('holding-shares').value = String(holding.shares ?? '');
    byId('holding-avg-cost').value = String(holding.avg_cost ?? '');
    byId('holding-acquired-on').value = holding.acquired_on ?? '';
    byId('holding-note').value = holding.note ?? '';
    holdingModal.open({
        focus: holding.symbol ? byId('holding-shares') : symbolField, opener, fallbackFocus: byId('portfolio-add'),
        async onSubmit() {
            const symbol = readSymbol('holding-symbol');
            const date = byId('holding-acquired-on');
            if (date.validity?.badInput || date.validity?.rangeOverflow) throw new Error('有効な取得日を入力してください。');
            const data = {
                shares: readNumber('holding-shares', '保有数量は0より大きい数値を入力してください。'),
                avg_cost: readNumber('holding-avg-cost', '平均取得単価は0以上の数値を入力してください。', true),
                acquired_on: date.value || null, note: readNote('holding-note'),
            };
            if (!holding.id) data.symbol = symbol;
            try {
                await save(user, 'portfolio', holding.id, data);
            } catch (error) {
                if (error.status === 409 && user === state.currentUser) {
                    duplicate = { symbol, user, opener };
                    existing.classList.remove('hidden');
                }
                throw error;
            }
            showToast(holding.id ? '保有銘柄を更新しました。' : '保有銘柄を追加しました。', 'success');
            if (visible) void loadPortfolio(user);
        },
    });
}

async function editDuplicateHolding() {
    const target = duplicate;
    if (!target || target.user !== state.currentUser) return;
    const button = byId('holding-edit-existing');
    button.disabled = true;
    try {
        const data = await fetchAPI(path(target.user, 'portfolio'), { cache: 'no-store' });
        if (duplicate !== target || byId('holding-editor-overlay').classList.contains('hidden')) return;
        const holding = data.holdings.find(item => item.symbol === target.symbol);
        if (!holding) throw new Error('登録済みの銘柄が見つかりません。');
        editHolding(target.opener, holding);
    } catch (error) {
        if (duplicate === target) feedback('holding-editor-error', error.message);
    } finally {
        if (duplicate === target) button.disabled = false;
    }
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
        focus: alert.symbol ? byId('alert-kind') : byId('alert-symbol'), opener, fallbackFocus: byId('portfolio-alert-add'),
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

function confirmDelete(opener, section, item) {
    const user = state.currentUser;
    if (!user) return;
    const isHolding = section === 'portfolio';
    const label = isHolding ? '保有銘柄' : '価格アラート';
    const message = byId('investment-delete-message');
    message.textContent = `${item.symbol} の${label}を削除しますか？`;
    deleteModal.open({
        focus: message, opener, fallbackFocus: byId(isHolding ? 'portfolio-add' : 'portfolio-alert-add'),
        async onSubmit() {
            if (user !== state.currentUser) throw new Error('ログイン状態が変更されました。');
            await fetchAPI(path(user, `${section}/${encodeURIComponent(item.id)}`), { method: 'DELETE' });
            showToast(`${label}を削除しました。`, 'success');
            if (visible) void (isHolding ? loadPortfolio(user) : loadAlerts(user));
        },
    });
}

export function bindPortfolioEvents() {
    holdingModal.bind();
    alertModal.bind();
    deleteModal.bind();
    refreshNotification = bindNotificationButton(byId('alert-notification-enable'), byId('alert-notification-status'));
    byId('portfolio-login-btn').addEventListener('click', event => openAuthModal('login', event.currentTarget));
    byId('portfolio-add').addEventListener('click', event => editHolding(event.currentTarget));
    byId('portfolio-alert-add').addEventListener('click', event => editAlert(event.currentTarget));
    byId('btn-detail-holding').addEventListener('click', event => {
        const quote = getDetailQuote();
        if (quote) editHolding(event.currentTarget, { symbol: quote.symbol, avg_cost: quote.price });
    });
    byId('btn-detail-alert').addEventListener('click', event => {
        const quote = getDetailQuote();
        if (quote) editAlert(event.currentTarget, { symbol: quote.symbol, threshold: quote.price });
    });
    byId('holding-edit-existing').addEventListener('click', editDuplicateHolding);
    byId('holding-symbol').addEventListener('input', () => {
        duplicate = null;
        byId('holding-edit-existing').classList.add('hidden');
    });
    byId('alert-kind').addEventListener('change', updateThresholdLabel);
    byId('portfolio-refresh').addEventListener('click', () => {
        if (state.currentUser) void loadPortfolio(state.currentUser);
    });
    byId('portfolio-csv').addEventListener('click', async () => {
        const user = state.currentUser;
        if (!user) return;
        try {
            await downloadAPI(path(user, 'portfolio/export.csv'), `portfolio-${new Date().toISOString().slice(0, 10)}.csv`);
        } catch (error) {
            if (user === state.currentUser) feedback('portfolio-error', error.message);
        }
    });
    for (const header of document.querySelectorAll('#portfolio-table th[data-sort]')) {
        header.querySelector('button').addEventListener('click', () => {
            ascending = sortKey !== header.dataset.sort || !ascending;
            sortKey = header.dataset.sort;
            renderHoldingTable(holdings, sortKey, ascending);
        });
    }
    for (const [id, section, edit, rows, endpoint] of [
        ['portfolio-holdings', 'holding', editHolding, () => holdings, 'portfolio'],
        ['portfolio-alerts', 'alert', editAlert, () => alerts, 'alerts'],
    ]) {
        byId(id).addEventListener('click', event => {
            const button = event.target.closest('button');
            if (!button) return;
            const editing = button.getAttribute(`data-edit-${section}`);
            const deleting = button.getAttribute(`data-delete-${section}`);
            const item = rows().find(row => String(row.id) === (editing ?? deleting));
            if (!item) return;
            if (editing) edit(button, item);
            else confirmDelete(button, endpoint, item);
        });
    }
}
