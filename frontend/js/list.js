/**
 * Stock Analyzer — Watchlist and holdings
 */

import { state } from './state.js';
import { downloadAPI, fetchAPI } from './api.js';
import { createActionModal } from './action-modal.js';
import { createDialog } from './dialog.js';
import { showToast, formatCurrency, formatLargeNumber, escapeHtml, escapeAttr, oldestStaleData, formatNewsDate } from './utils.js';
import { getMarketCapCategory, getMixIndexRating, getRatingSymbol } from './detail.js';
import { DEFAULT_PERIOD, normalizeSymbol, serializeRoute } from './router.js';

const RATE_LIMIT_MESSAGE = 'アクセスが集中しています。しばらくしてから再試行してください。';

const byId = id => document.getElementById(id);
let activeList = null;
const amountEdits = new Map();
const watchlistModal = createActionModal('watchlist-action');
const addDialog = createDialog('list-add-modal-overlay');

function listRoot(user = state.currentUser) {
    return `/user/${encodeURIComponent(user.id)}/lists`;
}

function selectionKey(user) {
    return `stockAnalyzerWatchlist:${user.id}`;
}

function savedListId(user) {
    try { return localStorage.getItem(selectionKey(user)); } catch { return null; }
}

function setListError(message = '') {
    const error = document.getElementById('watchlist-error');
    error.textContent = message;
    error.classList.toggle('hidden', !message);
}

export function renderWatchlistSelector() {
    const select = document.getElementById('watchlist-select');
    select.innerHTML = state.watchlists.map(list =>
        `<option value="${escapeAttr(list.id)}">${escapeHtml(list.name)}</option>`
    ).join('');
    select.value = state.currentListId ?? '';
    document.getElementById('watchlist-manager').classList.toggle('hidden', !state.currentUser);
    document.getElementById('watchlist-rename').disabled = !state.currentListId;
    document.getElementById('watchlist-delete').disabled = !state.currentListId || state.defaultListId === state.currentListId;
    document.getElementById('watchlist-csv').disabled = !state.currentListId;
}

function clearListRows() {
    clearTimeout(state.pendingDeleteTimer);
    state.pendingDeleteTimer = null;
    state.pendingDeleteSymbol = null;
    state.listItems = [];
    state.listCash = null;
    state.listPortfolio = null;
    amountEdits.clear();
    showRefreshStatus();
    renderListTable();
    document.dispatchEvent?.(new Event('watchlistchange'));
}

export function resetListSession() {
    watchlistModal.close();
    activeList?.controller.abort();
    activeList = null;
    state.currentListId = null;
    state.defaultListId = null;
    state.watchlists = [];
    clearListRows();
    renderWatchlistSelector();
    setListError();
}

export async function loadDefaultList(preferredId) {
    const requestUser = state.currentUser;
    if (!requestUser) return;

    try {
        const data = await fetchAPI(listRoot(requestUser), { cache: 'no-store' });
        if (state.currentUser !== requestUser) return;
        state.watchlists = data.lists;
        state.defaultListId = data.default_list_id;
        if (!data.lists.length) {
            const fallback = await fetchAPI(`/user/${encodeURIComponent(requestUser.id)}/default-list`, { cache: 'no-store' });
            if (state.currentUser !== requestUser) return;
            state.watchlists = [fallback];
            state.defaultListId = fallback.id;
        }
        const wanted = preferredId ?? savedListId(requestUser);
        const selected = state.watchlists.find(list => String(list.id) === String(wanted));
        const fallbackId = state.defaultListId ?? state.watchlists[0].id;
        await selectWatchlist(selected?.id ?? fallbackId);
    } catch (err) {
        if (state.currentUser === requestUser) {
            renderWatchlistSelector();
            setListError(err.message);
        }
    }
}

function listContext(user, id) {
    const controller = new AbortController();
    const context = {
        user, id, controller, summary: `${listRoot(user)}/${encodeURIComponent(id)}/summary?cached=true`,
        current: () => activeList === context && state.currentUser === user,
        valuationRequest: 0,
        refreshing: 0,
        ready: false,
    };
    return context;
}

export async function selectWatchlist(id) {
    const user = state.currentUser;
    if (!user || !state.watchlists.some(list => list.id === id)) return;
    const ready = id === state.currentListId && activeList?.current() && activeList.ready;
    activeList?.controller.abort();
    activeList = null;
    if (id !== state.currentListId) clearListRows();
    const context = activeList = listContext(user, id);
    context.ready = Boolean(ready);
    state.currentListId = id;
    try { localStorage.setItem(selectionKey(user), String(id)); } catch { /* storage unavailable */ }
    renderWatchlistSelector();
    setListError();
    showRefreshStatus();
    renderCash();

    try {
        const data = await fetchAPI(context.summary, { cache: 'no-store', signal: context.controller.signal });
        if (!context.current()) return;
        state.listItems = data.items.map(item => ({ ...item, loadError: item.error ?? null }));
        context.ready = true;
        state.listCash = data.cash_jpy ?? null;
        state.listPortfolio = data.portfolio ?? null;
        renderListTable();
        document.dispatchEvent?.(new Event('watchlistchange'));
        void refreshMarket(context);
    } catch (err) {
        if (context.current()) setListError(err.status === 429 ? RATE_LIMIT_MESSAGE : err.message);
    }
}

async function refreshValuation(context) {
    const request = ++context.valuationRequest;
    const data = await fetchAPI(context.summary, { cache: 'no-store', signal: context.controller.signal });
    if (!context.current() || request !== context.valuationRequest) return;
    const values = new Map(data.items.map(item => [item.symbol, item]));
    for (const item of state.listItems) {
        const valuation = values.get(item.symbol);
        if (valuation) {
            const { value_jpy, weight_pct, warning } = valuation;
            Object.assign(item, { value_jpy, weight_pct, warning });
        }
    }
    state.listPortfolio = data.portfolio ?? null;
    renderListTable();
}

const updateValuation = context => refreshValuation(context).catch(error => {
    if (context.current()) setListError(error.message);
});

function showRefreshStatus(message = '') {
    byId('watchlist-refresh').textContent = message;
}

async function refreshRow(item, isCurrent, signal) {
    try {
        const data = await fetchAPI(`/stock/${encodeURIComponent(item.symbol)}/snapshot`, { cache: 'no-store', signal });
        if (!isCurrent()) return;
        // Keep any older cache displayed when one market-data source fails.
        const { name, currency, stale, cached_at, error, ...fields } = data;
        Object.assign(item, fields, {
            name: name ?? item.name,
            currency: currency ?? item.currency,
            pending: false,
            loadError: error ?? null,
            stale: Boolean(stale || (error && item.stale)),
            cached_at: cached_at ?? (error ? item.cached_at : null),
        });
    } catch (error) {
        if (!isCurrent()) return;
        item.pending = false;
        item.loadError = error.status === 429 ? RATE_LIMIT_MESSAGE : '銘柄データを取得できませんでした。';
    }
    if (isCurrent()) renderListTable();
}

async function refreshMarket(context, { fxOnly = false } = {}) {
    const queue = fxOnly ? [] : state.listItems.filter(item => item.stale || item.pending);
    const fx = state.listPortfolio?.usd_jpy;
    let fxRefresh;
    function refreshFX() {
        const needsFX = state.listItems.some(item => item.shares != null && item.currency === 'USD');
        if (!fxRefresh && !context.fxRequested && needsFX && (!fx || fx.stale)) {
            context.fxRequested = true;
            fxRefresh = fetchAPI('/forex/usdjpy', {
                anonymous: true, cache: 'no-store', signal: context.controller.signal,
            }).catch(() => null);
        }
    }
    refreshFX();
    if (!queue.length && !fxRefresh) return;
    context.refreshing++;
    showRefreshStatus('更新中…');

    let timer = null;
    let intermediate;
    async function worker() {
        while (queue.length && context.current()) {
            await refreshRow(queue.shift(), context.current, context.controller.signal);
            if (!context.current()) return;
            refreshFX();
            if (timer === null) {
                timer = setTimeout(() => {
                    timer = null;
                    if (context.current()) intermediate = updateValuation(context);
                }, 1500);
            }
        }
    }
    await Promise.all(Array.from({ length: Math.min(queue.length, 4) }, worker));
    await fxRefresh;
    clearTimeout(timer);
    if (!context.current()) return;
    await intermediate;
    await updateValuation(context);
    if (--context.refreshing === 0 && context.current()) showRefreshStatus();
}

function editWatchlist(action, opener) {
    const user = state.currentUser;
    if (!user) return;
    const list = state.watchlists.find(entry => entry.id === state.currentListId);
    if (!list && action !== 'create') return;
    if (action === 'delete' && list.id === state.defaultListId) return;

    const root = listRoot(user);
    const endpoint = action === 'create' ? root : `${root}/${encodeURIComponent(list.id)}`;
    setListError();
    const deleting = action === 'delete';
    const title = { create: 'リストを新規作成', rename: 'リスト名を変更', delete: 'リストを削除' }[action];
    const input = document.getElementById('watchlist-action-name');
    const message = document.getElementById('watchlist-action-message');
    const submit = document.getElementById('watchlist-action-submit');
    document.getElementById('watchlist-action-title').textContent = title;
    document.getElementById('watchlist-action-fields').classList.toggle('hidden', deleting);
    input.value = action === 'rename' ? list.name : '';
    message.textContent = deleting ? `「${list.name}」を削除しますか？` : '';
    message.classList.toggle('hidden', !deleting);
    submit.textContent = { create: '作成', rename: '保存', delete: '削除' }[action];
    submit.classList.toggle('account-action-danger', deleting);

    watchlistModal.open({
        focus: deleting ? message : input,
        opener,
        fallbackFocus: document.getElementById('watchlist-select'),
        async onSubmit() {
            if (state.currentUser !== user) return;
            if (deleting) {
                await fetchAPI(endpoint, { method: 'DELETE' });
                await loadDefaultList(state.defaultListId);
                return;
            }
            const name = input.value.trim();
            if (!name || [...name].length > 50) throw new Error('リスト名は1〜50文字で入力してください');
            const saved = await fetchAPI(endpoint, {
                method: action === 'create' ? 'POST' : 'PUT', body: JSON.stringify({ name }),
            });
            await loadDefaultList(saved.id);
        },
    });
}

async function downloadWatchlist() {
    const user = state.currentUser;
    const id = state.currentListId;
    if (!user || !id) return;
    setListError();
    try {
        await downloadAPI(`${listRoot(user)}/${encodeURIComponent(id)}/export.csv`,
            `watchlist-${id}-${new Date().toISOString().slice(0, 10)}.csv`);
    } catch (err) {
        if (state.currentUser === user) setListError(err.message);
    }
}

const guestQueue = [];
let guestWorkers = 0;

async function refreshGuestRows() {
    if (guestWorkers >= 4) return;
    guestWorkers++;
    while (guestQueue.length) {
        const item = guestQueue.shift();
        const isCurrent = () => !state.currentUser && state.listItems.includes(item);
        if (isCurrent()) {
            showRefreshStatus('更新中…');
            await refreshRow(item, isCurrent);
        }
    }
    guestWorkers--;
    if (!guestWorkers && !state.currentUser) showRefreshStatus();
}

function getFilteredItems() {
    const query = state.listSearchQuery.trim().toLowerCase();
    const or = query.includes('|');
    const terms = query.split(or ? /\|/ : /\s+/).map(term => term.trim()).filter(Boolean);
    if (!terms.length) return state.listItems;
    return state.listItems.filter(item => {
        const text = [item.symbol, item.name, item.country].join(' ').toLowerCase();
        return or ? terms.some(term => text.includes(term)) : terms.every(term => text.includes(term));
    });
}

export function quickAddToList(symbol, opener) {
    openListAddModal(symbol, opener);
}

export function openListAddModal(prefill = '', opener) {
    const input = document.getElementById('list-add-modal-input');
    input.value = (prefill || '').toUpperCase();
    addDialog.open(input, { opener, fallbackFocus: document.getElementById('list-add-btn') });
}

export function closeListAddModal() {
    addDialog.close();
}

export async function submitListAddFromModal() {
    const input = document.getElementById('list-add-modal-input');
    const symbol = normalizeSymbol(input.value);
    if (!symbol) {
        showToast('有効なティッカーを入力してください', 'warning');
        return;
    }

    const success = await addSymbolToList(symbol);
    if (success) {
        input.value = '';
        closeListAddModal();
    }
}

async function addSymbolToList(symbol) {
    if (!symbol) return false;

    if (state.listItems.find(item => item.symbol === symbol)) {
        showToast(`${symbol} は既に追加済みです`, 'info');
        return false;
    }

    const requestUser = state.currentUser;
    if (requestUser) {
        if (!state.currentListId) await loadDefaultList();
        const id = state.currentListId;
        if (!id || state.currentUser !== requestUser) return false;
        try {
            await fetchAPI(`${listRoot(requestUser)}/${encodeURIComponent(id)}/items`, {
                method: 'POST', body: JSON.stringify({ symbol, tags: [] }),
            });
            if (state.currentUser !== requestUser || state.currentListId !== id) return false;
            await selectWatchlist(id);
            return state.currentUser === requestUser && state.currentListId === id;
        } catch (err) {
            if (state.currentUser === requestUser) showToast(err.message, 'error');
            return false;
        }
    }
    const item = { symbol, pending: true, tags: [] };
    state.listItems.push(item);
    renderListTable();
    document.dispatchEvent(new Event('watchlistchange'));
    guestQueue.push(item);
    void refreshGuestRows();
    return true;
}

async function removeFromList(symbol) {
    const context = activeList;
    if (context?.current()) {
        try {
            await fetchAPI(`${listRoot(context.user)}/${encodeURIComponent(context.id)}/items/${encodeURIComponent(symbol)}`,
                { method: 'DELETE' });
        } catch (error) {
            if (context.current()) setListError(error.message);
            return;
        }
        if (!context.current()) return;
    }
    state.listItems = state.listItems.filter(item => item.symbol !== symbol);
    amountEdits.delete(symbol);
    state.pendingDeleteSymbol = null;
    clearTimeout(state.pendingDeleteTimer);
    state.pendingDeleteTimer = null;
    renderListTable();
    document.dispatchEvent(new Event('watchlistchange'));
    if (context?.current()) void updateValuation(context);
}

export function requestRemoveFromList(symbol) {
    if (state.pendingDeleteSymbol === symbol) {
        removeFromList(symbol);
        return;
    }

    state.pendingDeleteSymbol = symbol;
    if (state.pendingDeleteTimer) {
        clearTimeout(state.pendingDeleteTimer);
    }
    state.pendingDeleteTimer = setTimeout(() => {
        state.pendingDeleteSymbol = null;
        state.pendingDeleteTimer = null;
        renderListTable();
    }, 4000);

    renderListTable();
}

export function sortTable(key) {
    if (state.sortKey === key) {
        state.sortAsc = !state.sortAsc;
    } else {
        state.sortKey = key;
        state.sortAsc = true;
    }
    renderListTable();
}

const yen = new Intl.NumberFormat('ja-JP', { maximumFractionDigits: 0 });
const percent = value => value == null ? '—' : `${value.toFixed(2)}%`;
const formatYen = value => value == null ? '—' : yen.format(value);
const cashKey = null;

function renderPortfolio() {
    const panel = byId('watchlist-portfolio');
    const portfolio = state.listPortfolio;
    panel.classList.toggle('hidden', !portfolio);
    if (!portfolio) {
        panel.innerHTML = '';
        return;
    }
    const totals = [
        ['評価額合計', `${formatYen(portfolio.total_value_jpy)} 円`],
        ['株式', `${formatYen(portfolio.stocks_value_jpy)} 円`],
        ['現金', `${formatYen(portfolio.cash_jpy)} 円`],
        ['現金比率', percent(portfolio.cash_ratio_pct)],
    ];
    const metrics = totals.map(([label, value]) => `<div class="metric-card"><dt class="metric-label">${label}</dt>
        <dd class="metric-value">${escapeHtml(value)}</dd></div>`).join('');
    const groups = [
        ['地域・現金', portfolio.by_region],
        ['通貨（円/ドル）', portfolio.by_currency.map(row => ({
            ...row, label: ({ JPY: '円（JPY）', USD: 'ドル（USD）' })[row.label] ?? row.label,
        }))],
        ['セクター（株式内）', portfolio.by_sector.slice(0, 5)],
    ];
    const weights = groups.map(([title, rows]) => `<section class="watchlist-allocation"><h3>${title}</h3>
        ${rows.length ? `<ul>${rows.map(row => {
            const value = escapeAttr(row.pct == null ? 0 : Math.max(0, Math.min(100, row.pct)));
            return `<li><span>${escapeHtml(row.label)}</span><span>${escapeHtml(formatYen(row.value_jpy))} 円 · ${escapeHtml(percent(row.pct))}</span>
                <meter aria-hidden="true" min="0" max="100" value="${value}"></meter></li>`;
        }).join('')}</ul>` : '<p class="text-muted">評価済みの株式はありません</p>'}</section>`).join('');
    const { largest, top_5_pct: topFive, holding_count: count } = portfolio.concentration;
    const fx = portfolio.usd_jpy;
    const excluded = portfolio.excluded;
    panel.innerHTML = `<dl class="metrics-grid watchlist-totals">${metrics}</dl>
        <div class="watchlist-allocations">${weights}</div>
        <dl class="watchlist-risk">
            <div><dt>最大保有比率</dt><dd>${largest ? `${escapeHtml(largest.symbol)} · ${escapeHtml(percent(largest.pct))}` : '—'}</dd></div>
            <div><dt>上位5銘柄比率</dt><dd>${escapeHtml(percent(topFive))}</dd></div>
            <div><dt>保有銘柄数</dt><dd>${escapeHtml(count)}</dd></div>
            <div><dt>USD/JPY</dt><dd>${fx ? `1ドル = ${escapeHtml(fx.rate.toLocaleString('ja-JP', { maximumFractionDigits: 4 }))} 円` : '—'}
                ${fx?.stale ? `<small class="stale-notice text-muted">キャッシュ (${escapeHtml(formatNewsDate(fx.cached_at) || fx.cached_at || '時刻不明')})</small>` : ''}</dd></div>
        </dl>
        ${excluded.length ? `<p class="watchlist-exclusion">集計から除外</p><ul class="watchlist-exclusion">
            ${excluded.map(row => `<li>${escapeHtml(row.symbol)} · ${escapeHtml(row.reason)}</li>`).join('')}</ul>` : ''}`;
}

function fieldErrorId(key) {
    return key === cashKey ? 'watchlist-cash-error' : `shares-error-${key}`;
}

function showAmountError(input, key, error = '') {
    if (input) input.setAttribute('aria-invalid', String(Boolean(error)));
    const message = byId(fieldErrorId(key));
    if (message) message.textContent = error;
}

function renderCash() {
    byId('watchlist-cash').classList.toggle('hidden', !activeList?.ready || !activeList.current());
    const input = byId('watchlist-cash-input');
    const edit = amountEdits.get(cashKey);
    if (document.activeElement !== input) {
        input.value = edit?.draft ?? (state.listCash == null ? '' : formatYen(state.listCash));
    }
    showAmountError(input, cashKey, edit?.error);
}

function sharesCell(item) {
    if (!state.currentUser) return '—';
    const symbol = escapeAttr(item.symbol);
    const edit = amountEdits.get(item.symbol);
    const error = escapeHtml(edit?.error);
    const id = escapeAttr(`shares-${item.symbol}`);
    return `<label class="visually-hidden" for="${id}">${symbol} の株数</label>
        <input id="${id}" class="watchlist-amount shares-input" type="number" step="any" min="0.0001" max="1000000000" autocomplete="off"
        data-shares-input="${symbol}" aria-describedby="${escapeAttr(fieldErrorId(item.symbol))}"
        aria-invalid="${Boolean(error)}" value="${escapeAttr(edit?.draft ?? item.shares)}">
        <p class="watchlist-field-error" id="${escapeAttr(fieldErrorId(item.symbol))}" role="alert">${error}</p>`;
}

function rowMarkup(item) {
    const name = escapeHtml(item.symbol);
    const route = serializeRoute({ view: 'detail', symbol: item.symbol });
    const isPendingDelete = state.pendingDeleteSymbol === item.symbol;
    const deleteLabel = isPendingDelete ? '削除を確定' : '削除';
    const placeholder = item.pending ? '…' : '—';
    const category = getMarketCapCategory(item.market_cap, item.currency);
    const negativePbr = item.pbr < 0;
    const negativePer = item.per < 0;
    const mixRating = item.pbr <= 0 || item.per <= 0 ? null : getMixIndexRating(item.mix_index);
    const mixValue = mixRating ? item.mix_index.toFixed(2) : placeholder;
    const mixBadge = mixRating ? ` <span class="indicator-badge ${mixRating}">${getRatingSymbol(mixRating)}</span>` : '';
    const pbrNote = negativePbr ? ' <small class="text-muted">債務超過</small>' : '';
    const perNote = negativePer ? ' <small class="text-muted">赤字</small>' : '';
    const price = item.price == null ? placeholder : item.currency
        ? formatCurrency(item.price, item.currency) : item.price.toLocaleString('ja-JP');
    const notes = [item.loadError, item.warning].filter(Boolean);
    const usesExchangeRate = item.currency === 'USD' && (item.price != null || item.market_cap != null);
    const forex = usesExchangeRate && { stale: true, cached_at: state.exchangeRateCachedAt };
    const stale = oldestStaleData(item, forex);
    if (stale) notes.push(`キャッシュ (${formatNewsDate(stale.cached_at) || stale.cached_at})`);
    const feedback = notes.map(note => `<small class="stale-notice text-muted">${escapeHtml(note)}</small>`).join('');
    const awaitingValue = item.shares != null && item.pending ? '…' : '—';

    return `<tr data-symbol="${escapeAttr(item.symbol)}">
        <td class="table-ticker"><a href="${escapeAttr(route)}">${name}</a></td>
        <td class="table-company">${escapeHtml(item.name || placeholder)}${feedback}</td>
        <td class="numeric">${escapeHtml(price)}</td>
        <td class="table-market-cap" title="${escapeAttr(item.market_cap != null ? formatLargeNumber(item.market_cap, item.currency) : '')}">${escapeHtml(category || placeholder)}</td>
        <td class="numeric">${mixValue}${mixBadge}${pbrNote}${perNote}</td>
        <td class="numeric">${item.dividend_yield != null ? item.dividend_yield.toFixed(2) + '%' : placeholder}</td>
        <td class="numeric watchlist-shares">${sharesCell(item)}</td>
        <td class="numeric">${item.value_jpy == null ? awaitingValue : escapeHtml(formatYen(item.value_jpy))}</td>
        <td class="numeric">${item.weight_pct == null ? awaitingValue : escapeHtml(percent(item.weight_pct))}</td>
        <td><button class="btn-remove ${isPendingDelete ? 'confirm' : ''}" type="button" data-action="remove-item"
            data-symbol="${escapeAttr(item.symbol)}" aria-label="${deleteLabel}（${escapeAttr(item.symbol)}）">${deleteLabel}</button></td>
    </tr>`;
}

function updateTableRows(tbody, markup) {
    const active = document.activeElement;
    const focused = active && tbody.contains(active);
    // Keep the live input and its caret while quotes arrive in the current sort order.
    if (focused && tbody.rows && active.dataset.sharesInput) {
        const staged = document.createElement('tbody');
        staged.innerHTML = markup;
        const rows = [...tbody.rows];
        if (rows.length === staged.rows.length &&
            rows.every((row, index) => row.dataset.symbol === staged.rows[index].dataset.symbol)) {
            rows.forEach((row, index) => {
                const next = [...staged.rows[index].cells];
                [...row.cells].forEach((cell, column) => {
                    if (!cell.contains(active) && cell.outerHTML !== next[column].outerHTML) cell.replaceWith(next[column]);
                });
            });
            showAmountError(active, active.dataset.sharesInput, amountEdits.get(active.dataset.sharesInput)?.error);
            return;
        }
    }
    tbody.innerHTML = markup;
    if (!focused) return;
    const key = active.dataset.sharesInput ?? active.closest('tr')?.dataset.symbol;
    const target = [...tbody.querySelectorAll('tr[data-symbol]')].find(row => row.dataset.symbol === key);
    const selector = active.dataset.sharesInput ? '[data-shares-input]' : active.dataset.action ? 'button' : 'a';
    (target?.querySelector(selector) ?? byId('list-add-btn'))?.focus();
}

export function renderListTable() {
    renderPortfolio();
    renderCash();
    const empty = !state.listItems.length;
    byId('list-empty').classList.toggle('hidden', !empty);
    byId('list-table-section').classList.toggle('hidden', empty);

    const sorted = [...getFilteredItems()].sort((a, b) => {
        const va = a[state.sortKey];
        const vb = b[state.sortKey];
        if (va == null) return vb == null ? 0 : 1;
        if (vb == null) return -1;
        const order = typeof va === 'string' ? va.localeCompare(vb) : va - vb;
        return state.sortAsc ? order : -order;
    });

    document.querySelectorAll('#comparison-table th.sortable').forEach(th => {
        const selected = th.dataset.sort === state.sortKey;
        th.classList.toggle('sort-active', selected);
        const arrow = th.querySelector('.sort-arrow');
        if (selected) th.setAttribute('aria-sort', state.sortAsc ? 'ascending' : 'descending');
        else th.removeAttribute('aria-sort');
        arrow.textContent = selected ? (state.sortAsc ? ' ▲' : ' ▼') : '';
    });
    const markup = empty ? '' : sorted.length ? sorted.map(rowMarkup).join('')
        : '<tr><td colspan="10" class="text-muted">検索条件に一致する銘柄がありません</td></tr>';
    updateTableRows(byId('comparison-tbody'), markup);
}

function trackAmount(input, key) {
    const context = activeList;
    if (!context?.ready || !context.current()) return null;
    let edit = amountEdits.get(key);
    if (!edit) {
        const saved = key === cashKey ? state.listCash : state.listItems.find(item => item.symbol === key)?.shares;
        edit = { context, saved: saved ?? null };
        amountEdits.set(key, edit);
    }
    const draft = input.value.trim();
    if (draft !== edit.draft) {
        edit.draft = draft;
        edit.error = '';
        showAmountError(input, key);
    }
    return edit;
}

function parseAmount(draft, key, badInput) {
    const cash = key === cashKey;
    const raw = cash ? draft.replace(/,/g, '') : draft;
    if (!draft && !badInput) return null;
    const value = Number(raw);
    if (cash && !badInput && /^(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)$/.test(draft) &&
        Number.isSafeInteger(value) && value >= 0 && value <= 1e13) return value;
    if (!cash && !badInput && /^[+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:e[+-]?[0-9]+)?$/i.test(raw) &&
        value > 0 && value <= 1e9 && Number(value.toFixed(4)) === value) return value;
    throw new Error(cash ? '現金は0円以上10兆円以下の整数で入力してください'
        : '株数は0より大きく10億以下の数値を小数第4位までで入力してください');
}

function fieldInput(key) {
    if (key === cashKey) return byId('watchlist-cash-input');
    return [...byId('comparison-tbody').querySelectorAll('[data-shares-input]')]
        .find(input => input.dataset.sharesInput === key);
}

async function persistAmount(key, edit) {
    const { context } = edit;
    if (edit.saving || edit.timer != null || !edit.queued || state.currentUser !== context.user) return;
    const { value, draft } = edit.queued;
    edit.queued = null;
    if (edit.saved === value) return;
    const live = () => activeList?.current() && amountEdits.get(key) === edit;
    const cash = key === cashKey;
    const field = cash ? 'cash_jpy' : 'shares';
    const suffix = cash ? 'cash' : `items/${encodeURIComponent(key)}/shares`;
    edit.saving = true;
    try {
        const result = await fetchAPI(`${listRoot(context.user)}/${encodeURIComponent(context.id)}/${suffix}`, {
            method: 'PUT', body: JSON.stringify({ [field]: value }),
        });
        edit.saved = result[field];
        if (!live()) return;
        if (cash) state.listCash = edit.saved;
        else {
            const item = state.listItems.find(item => item.symbol === key);
            if (item) item.shares = edit.saved;
        }
        renderListTable();
        const current = activeList;
        void updateValuation(current).then(() => {
            if (current.current() && !cash && edit.saved != null) void refreshMarket(current, { fxOnly: true });
        });
    } catch (error) {
        if (!live() || edit.queued || edit.draft !== draft) return;
        edit.error = error.message;
        showAmountError(fieldInput(key), key, edit.error);
    } finally {
        edit.saving = false;
        if (edit.queued) void persistAmount(key, edit);
    }
}

function queueAmount(input, key, blur = false) {
    const edit = trackAmount(input, key);
    if (!edit) return;
    clearTimeout(edit.timer);
    edit.timer = null;
    edit.queued = null;
    try {
        const value = parseAmount(edit.draft, key, input.validity?.badInput);
        edit.error = '';
        showAmountError(input, key);
        if (key === cashKey && blur && value != null) {
            input.value = formatYen(value);
            edit.draft = input.value;
        }
        edit.queued = { value, draft: edit.draft };
        edit.timer = setTimeout(() => {
            edit.timer = null;
            void persistAmount(key, edit);
        }, 250);
    } catch (error) {
        edit.error = error.message;
        showAmountError(input, key, edit.error);
    }
}

export function goToDetail(symbol) {
    window.switchView('detail', symbol, DEFAULT_PERIOD);
}

export function setListSearchQuery(query) {
    state.listSearchQuery = query;
    renderListTable();
}

function bindAmountEditor(container, keyFor) {
    for (const eventName of ['input', 'change', 'focusout', 'keydown']) {
        container.addEventListener(eventName, event => {
            const key = keyFor(event.target);
            if (key === undefined) return;
            if (eventName === 'input') {
                trackAmount(event.target, key);
                return;
            }
            if (eventName === 'keydown') {
                if (event.key !== 'Enter' || event.isComposing || event.keyCode === 229) return;
                event.preventDefault();
            }
            queueAmount(event.target, key, eventName === 'focusout');
        });
    }
}

export function bindListEvents() {
    watchlistModal.bind();
    byId('watchlist-select')?.addEventListener('change', e => {
        const list = state.watchlists.find(entry => String(entry.id) === e.target.value);
        if (list) selectWatchlist(list.id);
    });
    for (const action of ['create', 'rename', 'delete']) {
        const button = byId(`watchlist-${action}`);
        button?.addEventListener('click', () => editWatchlist(action, button));
    }
    byId('watchlist-csv')?.addEventListener('click', downloadWatchlist);
    byId('list-add-btn')?.addEventListener('click', event => openListAddModal('', event.currentTarget));
    byId('list-add-modal-submit')?.addEventListener('click', submitListAddFromModal);
    byId('list-add-modal-close')?.addEventListener('click', closeListAddModal);
    addDialog.bind(closeListAddModal);

    const cashInput = byId('watchlist-cash-input');
    cashInput.addEventListener('focusin', () => {
        cashInput.value = cashInput.value.replace(/,/g, '');
    });
    bindAmountEditor(cashInput, () => cashKey);
    bindAmountEditor(byId('comparison-tbody'), target => target.dataset.sharesInput);

    document.querySelectorAll('#comparison-table th.sortable[data-sort]').forEach(th => {
        th.addEventListener('click', () => {
            const key = th.dataset.sort;
            if (key) sortTable(key);
        });
    });
    document.addEventListener('click', e => {
        const quickAdd = e.target.closest('[data-quick-add]');
        if (quickAdd) {
            quickAddToList(quickAdd.dataset.quickAdd, quickAdd);
            return;
        }
        const remove = e.target.closest('[data-action="remove-item"]');
        if (remove) {
            requestRemoveFromList(remove.dataset.symbol);
            return;
        }
        const row = e.target.closest('#comparison-tbody tr[data-symbol]');
        if (row && !e.target.closest('button, a, input, .watchlist-shares')) goToDetail(row.dataset.symbol);
    });
}
