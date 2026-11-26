/**
 * Stock Analyzer — List view and tag management
 */

import { state } from './state.js';
import { downloadAPI, fetchAPI } from './api.js';
import { createActionModal } from './action-modal.js';
import { createDialog } from './dialog.js';
import { showToast, formatCurrency, formatLargeNumber, escapeHtml, escapeAttr, normalizeTagHue, oldestStaleData, staleNotice } from './utils.js';
import { getMarketCapCategory, getMixIndexRating, getRatingSymbol } from './detail.js';
import { DEFAULT_PERIOD, serializeRoute } from './router.js';

const RATE_LIMIT_MESSAGE = 'アクセスが集中しています。しばらくしてから再試行してください。';

let summaryRequest = 0;
const watchlistModal = createActionModal('watchlist-action');
const memoModal = createActionModal('memo-edit');
const addDialog = createDialog('list-add-modal-overlay');
const filterDialog = createDialog('filter-modal-overlay');
const tagDialog = createDialog('tag-modal-overlay');

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
    state.activeTagFilter = null;
    state.listItems = [];
    closeTagModal();
    renderListTable();
    document.dispatchEvent?.(new Event('watchlistchange'));
}

export function resetListSession() {
    watchlistModal.close();
    memoModal.close();
    summaryRequest++;
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

export async function selectWatchlist(id) {
    const user = state.currentUser;
    if (!user || !state.watchlists.some(list => list.id === id)) return;
    const request = ++summaryRequest;
    if (id !== state.currentListId) clearListRows();
    state.currentListId = id;
    try { localStorage.setItem(selectionKey(user), String(id)); } catch { /* storage unavailable */ }
    renderWatchlistSelector();
    setListError();

    try {
        const data = await fetchAPI(`${listRoot(user)}/${encodeURIComponent(id)}/summary`, { cache: 'no-store' });
        if (state.currentUser !== user || summaryRequest !== request) return;
        state.listItems = data.items.map(item => ({ ...item, loadError: item.error ?? null }));
        renderListTable();
        document.dispatchEvent?.(new Event('watchlistchange'));
    } catch (err) {
        if (state.currentUser === user && summaryRequest === request) {
            setListError(err.status === 429 ? RATE_LIMIT_MESSAGE : err.message);
        }
    }
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

async function fetchGuestItemData(symbol) {
    const results = await Promise.allSettled([
        fetchAPI(`/stock/${encodeURIComponent(symbol)}`),
        fetchAPI(`/stock/${encodeURIComponent(symbol)}/profile`),
        fetchAPI(`/stock/${encodeURIComponent(symbol)}/indicators`),
    ]);
    const [quote, profile, indicators] = results.map(result => result.value);
    const failures = results.filter(result => result.status === 'rejected');
    const rateLimited = failures.some(result => result.reason?.status === 429);
    const cached = oldestStaleData(quote, profile, indicators);
    return {
        rateLimited,
        fields: {
            name: profile?.name,
            country: profile?.country,
            price: quote?.price,
            currency: profile?.currency,
            market_cap: profile?.market_cap,
            mix_index: indicators?.mix_index,
            per: indicators?.per,
            pbr: indicators?.pbr,
            dividend_yield: indicators?.dividend_yield,
            stale: !!cached,
            cached_at: cached?.cached_at,
            loadError: rateLimited ? RATE_LIMIT_MESSAGE : failures.length ? '一部のデータを取得できませんでした。' : null,
        },
    };
}

export function editStockMemo(symbol, opener) {
    const user = state.currentUser;
    if (!user) {
        showToast('メモ機能はログインが必要です', 'warning');
        return;
    }

    const input = document.getElementById('memo-edit-input');
    input.value = state.listItems.find(item => item.symbol === symbol)?.memo ?? '';
    document.getElementById('memo-edit-title').textContent = `${symbol} のメモを編集`;
    memoModal.open({
        focus: input,
        opener,
        fallbackFocus: document.getElementById('list-add-btn'),
        async onSubmit() {
            if (state.currentUser !== user) return;
            const saved = await fetchAPI(`/user/${encodeURIComponent(user.id)}/memo/${encodeURIComponent(symbol)}`, {
                method: 'PUT',
                body: JSON.stringify({ memo: input.value.trim() || null }),
            });
            const item = state.listItems.find(entry => entry.symbol === symbol);
            if (item) {
                item.memo = saved.memo || null;
                renderListTable();
            }
            showToast('メモを保存しました', 'success');
        },
    });
}

// ===== List View =====
class ListSearchQueryParser {
    parse(rawQuery) {
        const normalized = (rawQuery || '').trim();
        if (!normalized) {
            return { isEmpty: true, mode: 'AND', tagMode: false, terms: [] };
        }

        const tagMode = normalized.startsWith('#');
        const mode = normalized.includes('|') ? 'OR' : 'AND';
        const splitter = mode === 'OR' ? /\|/ : /\s+/;
        const terms = normalized
            .split(splitter)
            .map(term => term.trim())
            .filter(Boolean)
            .map(term => term.replace(/^#/, '').toLowerCase())
            .filter(Boolean);

        if (terms.length === 0) {
            return { isEmpty: true, mode, tagMode, terms: [] };
        }

        return { isEmpty: false, mode, tagMode, terms };
    }

    matches(item, parsed) {
        if (parsed.isEmpty) return true;

        const tags = (item.tags || []).map(tag => String(tag).toLowerCase());
        if (parsed.tagMode) {
            return parsed.mode === 'OR'
                ? parsed.terms.some(term => tags.some(tag => tag.includes(term)))
                : parsed.terms.every(term => tags.some(tag => tag.includes(term)));
        }

        const searchable = [
            item.symbol || '',
            item.name || '',
            item.country || '',
        ]
            .join(' ')
            .toLowerCase();

        return parsed.mode === 'OR'
            ? parsed.terms.some(term => searchable.includes(term))
            : parsed.terms.every(term => searchable.includes(term));
    }
}

const listSearchParser = new ListSearchQueryParser();

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
    const symbol = input.value.trim().toUpperCase();
    if (!symbol) return;

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
            if (state.currentUser === requestUser && state.currentListId === id) await selectWatchlist(id);
            return true;
        } catch (err) {
            if (state.currentUser === requestUser) showToast(err.message, 'error');
            return false;
        }
    }
    const item = { symbol, loading: true, tags: [] };
    state.listItems.push(item);
    renderListTable();

    const { fields, rateLimited } = await fetchGuestItemData(symbol);
    if (state.currentUser !== requestUser || !state.listItems.includes(item)) return false;
    if (!fields.loadError || rateLimited) {
        Object.assign(item, fields, { loading: false });
        renderListTable();
        document.dispatchEvent(new Event('watchlistchange'));
        return true;
    }

    state.listItems = state.listItems.filter(entry => entry !== item);
    renderListTable();
    showToast(`${symbol} の取得に失敗しました`, 'error');
    return false;
}

async function removeFromList(symbol) {
    state.listItems = state.listItems.filter(i => i.symbol !== symbol);
    if (state.pendingDeleteSymbol === symbol) {
        state.pendingDeleteSymbol = null;
    }
    if (state.pendingDeleteTimer) {
        clearTimeout(state.pendingDeleteTimer);
        state.pendingDeleteTimer = null;
    }
    renderListTable();
    document.dispatchEvent(new Event('watchlistchange'));

    // サーバーからも削除
    if (state.currentUser && state.currentListId) {
        try {
            await fetchAPI(`/user/${encodeURIComponent(state.currentUser.id)}/lists/${encodeURIComponent(state.currentListId)}/items/${encodeURIComponent(symbol)}`, { method: 'DELETE' });
        } catch (err) {
            console.warn('Server remove failed:', err);
        }
    }
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

function getFilteredItems() {
    const parsed = listSearchParser.parse(state.listSearchQuery);

    return state.listItems.filter(item => {
        const matchesTagFilter = !state.activeTagFilter || (item.tags && item.tags.includes(state.activeTagFilter));
        if (!matchesTagFilter) return false;
        return listSearchParser.matches(item, parsed);
    });
}

function getAllTags() {
    const tagSet = new Set();
    state.listItems.forEach(item => {
        if (item.tags) item.tags.forEach(t => tagSet.add(t));
    });
    return [...tagSet].sort();
}

function updateFilterButtonState() {
    const filterBar = document.getElementById('active-filter-bar');
    if (!filterBar) return;

    if (state.activeTagFilter) {
        // アクティブフィルタバーを表示
        filterBar.classList.remove('hidden');
        const tagEl = document.getElementById('active-filter-tag');
        const color = getTagColor(state.activeTagFilter);
        tagEl.innerHTML = `<span class="tag-chip small" data-tag-hue="${color}">${escapeHtml(state.activeTagFilter)}</span>`;
        applyTagHues(tagEl);
    } else {
        filterBar.classList.add('hidden');
    }
}

export function renderListTable() {
    const emptyEl = document.getElementById('list-empty');
    const tableEl = document.getElementById('list-table-section');
    const tbody = document.getElementById('comparison-tbody');
    const active = document.activeElement;
    const focusedAction = active && tbody.contains(active) ? active.dataset : null;

    if (state.listItems.length === 0) {
        emptyEl.classList.remove('hidden');
        tableEl.classList.add('hidden');
        updateFilterButtonState();
        if (focusedAction?.action) document.getElementById('list-add-btn')?.focus();
        return;
    }

    emptyEl.classList.add('hidden');
    tableEl.classList.remove('hidden');
    updateFilterButtonState();

    const filtered = getFilteredItems();
    const sorted = [...filtered].sort((a, b) => {
        if (a.loading) return 1;
        if (b.loading) return -1;
        let va = a[state.sortKey];
        let vb = b[state.sortKey];
        if (va == null) return 1;
        if (vb == null) return -1;
        if (typeof va === 'string') return state.sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
        return state.sortAsc ? va - vb : vb - va;
    });

    // ヘッダーの矢印更新
    document.querySelectorAll('#comparison-table th.sortable').forEach(th => {
        const key = th.dataset.sort;
        th.classList.toggle('sort-active', key === state.sortKey);
        const arrow = th.querySelector('.sort-arrow');
        if (key === state.sortKey) {
            th.setAttribute('aria-sort', state.sortAsc ? 'ascending' : 'descending');
            arrow.textContent = state.sortAsc ? ' ▲' : ' ▼';
        } else {
            th.removeAttribute('aria-sort');
            arrow.textContent = '';
        }
    });

    if (sorted.length === 0) {
        tbody.innerHTML = '<tr class="row-loading"><td colspan="9" class="text-muted">検索条件に一致する銘柄がありません</td></tr>';
        return;
    }

    let html = '';

    sorted.forEach(item => {
        const name = escapeHtml(item.symbol);
        const route = serializeRoute({ view: 'detail', symbol: item.symbol });
        const ticker = `<a href="${escapeAttr(route)}">${name}</a>`;
        const isPendingDelete = state.pendingDeleteSymbol === item.symbol;
        const deleteLabel = isPendingDelete ? '削除を確定' : '削除';
        const removeButton = `<button class="btn-remove ${isPendingDelete ? 'confirm' : ''}" type="button"
            data-action="remove-item" data-symbol="${name}" aria-label="${deleteLabel}（${name}）">${deleteLabel}</button>`;
        if (item.loading) {
            html += `
                <tr class="row-loading" data-symbol="${escapeHtml(item.symbol)}">
                    <td class="table-ticker">${ticker}</td>
                    <td colspan="7" class="text-muted">読み込み中...</td>
                    <td>${removeButton}</td>
                </tr>`;
            return;
        }

        let tagsHtml = '';
        if (item.tags && item.tags.length > 0) {
            item.tags.forEach(tag => {
                const color = getTagColor(tag);
                tagsHtml += `<span class="tag-chip small" data-tag-hue="${color}">${escapeHtml(tag)}</span>`;
            });
        }
        tagsHtml += `<button class="btn-tag-edit" type="button" data-action="open-tag-modal" data-symbol="${name}" aria-label="${name} のタグを編集" title="タグ編集">🏷️</button>`;

        const memoPreview = item.memo
            ? escapeHtml(item.memo.length > 24 ? `${item.memo.slice(0, 24)}...` : item.memo)
            : '<span class="memo-empty">未入力</span>';
        const memoTitle = item.memo ? escapeAttr(item.memo) : '';
        const marketCapCategory = getMarketCapCategory(item.market_cap, item.currency);
        const negativePbr = item.pbr < 0;
        const negativePer = item.per < 0;
        const mixRating = negativePbr || negativePer ? null : getMixIndexRating(item.mix_index);
        const mixValue = mixRating ? item.mix_index.toFixed(2) : '—';
        const mixBadge = mixRating ? ` <span class="indicator-badge ${mixRating}">${getRatingSymbol(mixRating)}</span>` : '';
        const pbrNote = negativePbr ? ' <small class="text-muted">債務超過</small>' : '';
        const perNote = negativePer ? ' <small class="text-muted">赤字</small>' : '';
        const price = item.price == null ? '—' : item.currency
            ? formatCurrency(item.price, item.currency) : item.price.toLocaleString('ja-JP');
        const loadError = item.loadError ? `<br><small class="text-muted">${escapeHtml(item.loadError)}</small>` : '';
        const usesExchangeRate = (item.currency === 'USD' && (item.price != null || item.market_cap != null))
            || ((!item.currency || item.currency === 'JPY') && item.market_cap != null);
        const forex = usesExchangeRate && { stale: true, cached_at: state.exchangeRateCachedAt };
        const notice = staleNotice(oldestStaleData(item, forex));

        html += `
            <tr data-symbol="${escapeHtml(item.symbol)}">
                <td class="table-ticker">${ticker}</td>
                <td class="table-company">${escapeHtml(item.name || '—')}${loadError}${notice}</td>
                <td class="numeric">${escapeHtml(price)}</td>
                <td class="table-market-cap" title="${item.market_cap != null ? escapeAttr(formatLargeNumber(item.market_cap, item.currency)) : ''}">${marketCapCategory || '—'}</td>
                <td class="numeric">${mixValue}${mixBadge}${pbrNote}${perNote}</td>
                <td class="numeric">${item.dividend_yield != null ? item.dividend_yield.toFixed(2) + '%' : '—'}</td>
                <td class="table-tags">${tagsHtml}</td>
                <td class="table-memo"><button class="btn-memo" type="button" data-action="edit-memo" data-symbol="${name}" aria-label="${name} のメモを編集">📝</button><span class="memo-preview" title="${memoTitle}">${memoPreview}</span></td>
                <td>${removeButton}</td>
            </tr>`;
    });

    tbody.innerHTML = html;
    applyTagHues(tbody);
    if (focusedAction?.action) {
        const { action, symbol } = focusedAction;
        const replacement = [...tbody.querySelectorAll('button[data-action]')]
            .find(button => button.dataset.action === action && button.dataset.symbol === symbol);
        (replacement ?? document.getElementById('list-add-btn'))?.focus();
    }
}

export function goToDetail(symbol) {
    window.switchView('detail', symbol, DEFAULT_PERIOD);
}

// ===== Filter Modal =====
export function openFilterModal() {
    const allTags = getAllTags();
    const container = document.getElementById('filter-tags-container');

    if (allTags.length === 0) {
        container.innerHTML = '<p class="filter-empty-note">タグが設定された銘柄がありません。<br>銘柄行の 🏷️ ボタンからタグを追加できます。</p>';
    } else {
        let html = '<div class="filter-tag-list">';
        allTags.forEach(tag => {
            const color = getTagColor(tag);
            const isActive = tag === state.activeTagFilter;
            const count = state.listItems.filter(i => i.tags && i.tags.includes(tag)).length;
            html += `<button class="filter-tag-item ${isActive ? 'active' : ''}" type="button" data-action="select-filter-tag" data-tag="${escapeAttr(tag)}" data-tag-hue="${color}">
                <span class="filter-tag-name">${escapeHtml(tag)}</span>
                <span class="filter-tag-count">${count}件</span>
            </button>`;
        });
        html += '</div>';
        container.innerHTML = html;
        applyTagHues(container);
    }

    filterDialog.open(document.getElementById('filter-modal-close'));
}

export function closeFilterModal() {
    filterDialog.close();
}

export function selectFilterTag(tag) {
    state.activeTagFilter = state.activeTagFilter === tag ? null : tag;

    // モーダル内のアクティブ状態を更新
    document.querySelectorAll('.filter-tag-item').forEach(el => {
        const nameEl = el.querySelector('.filter-tag-name');
        if (nameEl && nameEl.textContent === tag) {
            el.classList.toggle('active', state.activeTagFilter === tag);
        } else {
            el.classList.remove('active');
        }
    });

    renderListTable();
}

export function clearTagFilter() {
    state.activeTagFilter = null;
    renderListTable();
}

// ===== Tag Management =====
export function openTagModal(symbol, opener) {
    state.tagEditSymbol = symbol;
    document.getElementById('tag-modal-symbol').textContent = symbol;
    document.getElementById('tag-input').value = '';
    renderCurrentTags();
    tagDialog.open(document.getElementById('tag-input'), { opener, fallbackFocus: document.getElementById('list-search-input') });
}

export function closeTagModal() {
    tagDialog.close();
    state.tagEditSymbol = null;
}

function renderCurrentTags() {
    const container = document.getElementById('tag-current-tags');
    const item = state.listItems.find(i => i.symbol === state.tagEditSymbol);
    if (!item || !item.tags || item.tags.length === 0) {
        container.innerHTML = '<span class="tag-empty-note">タグがありません</span>';
        return;
    }

    let html = '';
    item.tags.forEach(tag => {
        const color = getTagColor(tag);
        html += `<span class="tag-chip editable" data-tag-hue="${color}">
            ${escapeHtml(tag)}
            <button class="tag-remove-btn" type="button" data-action="remove-tag" data-tag="${escapeAttr(tag)}" aria-label="${escapeAttr(tag)} タグを削除" title="削除">✕</button>
        </span>`;
    });
    container.innerHTML = html;
    applyTagHues(container);
}

export function addTagFromInput() {
    const input = document.getElementById('tag-input');
    const tag = input.value.trim();
    if (!tag) return;
    input.value = '';
    addTagToItem(state.tagEditSymbol, tag);
}

export function addPresetTag(tag) {
    addTagToItem(state.tagEditSymbol, tag);
}

function addTagToItem(symbol, tag) {
    const item = state.listItems.find(i => i.symbol === symbol);
    if (!item) return;
    if (!item.tags) item.tags = [];
    if (item.tags.includes(tag)) return;

    item.tags.push(tag);
    renderCurrentTags();
    renderListTable();
    syncTagsToServer(symbol, item.tags);
}

export function removeTag(tag) {
    const item = state.listItems.find(i => i.symbol === state.tagEditSymbol);
    if (!item || !item.tags) return;
    item.tags = item.tags.filter(t => t !== tag);
    renderCurrentTags();
    renderListTable();
    syncTagsToServer(state.tagEditSymbol, item.tags);
}

async function syncTagsToServer(symbol, tags) {
    if (!state.currentUser || !state.currentListId) return;
    try {
        await fetchAPI(`/user/${encodeURIComponent(state.currentUser.id)}/lists/${encodeURIComponent(state.currentListId)}/items/${encodeURIComponent(symbol)}/tags`, {
            method: 'PUT',
            body: JSON.stringify({ tags }),
        });
    } catch (err) {
        console.warn('Tag sync failed:', err);
    }
}

function applyTagHues(container) {
    container.querySelectorAll('[data-tag-hue]').forEach(el => {
        el.style.setProperty('--tag-hue', el.dataset.tagHue);
    });
}

function getTagColor(tag) {
    tag = String(tag ?? '');
    let hash = 0;
    for (let i = 0; i < tag.length; i++) {
        hash = tag.charCodeAt(i) + ((hash << 5) - hash);
    }
    return normalizeTagHue(Math.abs(hash % 360));
}

export function setListSearchQuery(query) {
    state.listSearchQuery = query;
    renderListTable();
}

export function bindListEvents() {
    watchlistModal.bind();
    memoModal.bind();
    document.getElementById('watchlist-select')?.addEventListener('change', e => {
        const list = state.watchlists.find(entry => String(entry.id) === e.target.value);
        if (list) selectWatchlist(list.id);
    });
    for (const action of ['create', 'rename', 'delete']) {
        const button = document.getElementById(`watchlist-${action}`);
        button?.addEventListener('click', () => editWatchlist(action, button));
    }
    document.getElementById('watchlist-csv')?.addEventListener('click', downloadWatchlist);
    document.getElementById('list-add-btn')?.addEventListener('click', event => openListAddModal('', event.currentTarget));
    document.getElementById('list-add-modal-submit')?.addEventListener('click', submitListAddFromModal);
    document.getElementById('active-filter-clear')?.addEventListener('click', clearTagFilter);

    document.querySelectorAll('#comparison-table th.sortable[data-sort]').forEach(th => {
        th.addEventListener('click', () => {
            const key = th.dataset.sort;
            if (key) sortTable(key);
        });
    });

    addDialog.bind(closeListAddModal);
    document.getElementById('list-add-modal-close')?.addEventListener('click', closeListAddModal);

    filterDialog.bind(closeFilterModal);
    document.getElementById('filter-modal-close')?.addEventListener('click', closeFilterModal);
    document.getElementById('filter-clear-btn')?.addEventListener('click', () => {
        clearTagFilter();
        closeFilterModal();
    });

    tagDialog.bind(closeTagModal);
    document.getElementById('tag-modal-close')?.addEventListener('click', closeTagModal);
    document.getElementById('tag-add-btn')?.addEventListener('click', addTagFromInput);

    document.addEventListener('click', e => {
        const quickAddEl = e.target.closest('[data-quick-add]');
        if (quickAddEl) {
            quickAddToList(quickAddEl.dataset.quickAdd, quickAddEl);
            return;
        }

        const presetTagBtn = e.target.closest('[data-preset-tag]');
        if (presetTagBtn) {
            addPresetTag(presetTagBtn.dataset.presetTag);
            return;
        }

        const filterTagBtn = e.target.closest('.filter-tag-item[data-tag]');
        if (filterTagBtn) {
            selectFilterTag(filterTagBtn.dataset.tag);
            return;
        }

        const actionBtn = e.target.closest('[data-action]');
        if (actionBtn) {
            const action = actionBtn.dataset.action;
            const symbol = actionBtn.dataset.symbol;
            const tag = actionBtn.dataset.tag;

            if (action === 'remove-item' && symbol) {
                requestRemoveFromList(symbol);
                return;
            }
            if (action === 'open-tag-modal' && symbol) {
                openTagModal(symbol, actionBtn);
                return;
            }
            if (action === 'edit-memo' && symbol) {
                editStockMemo(symbol, actionBtn);
                return;
            }
            if (action === 'remove-tag' && tag) {
                document.getElementById('tag-input').focus();
                removeTag(tag);
                return;
            }
        }

        const row = e.target.closest('#comparison-tbody tr[data-symbol]');
        if (row && !e.target.closest('button, a')) {
            const symbol = row.dataset.symbol;
            if (symbol) goToDetail(symbol);
        }
    });
}
