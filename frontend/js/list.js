/**
 * Stock Analyzer — List view and tag management
 */

import { state } from './state.js';
import { fetchAPI } from './api.js';
import { showToast, formatCurrency, formatLargeNumber, escapeHtml, escapeAttr } from './utils.js';
import { getMarketCapCategory, getMixIndexRating, getRatingSymbol, searchStock } from './detail.js';

// ===== Default List (1:1対応) =====
export async function loadDefaultList() {
    if (!state.currentUser) return;

    try {
        const data = await fetchAPI(`/user/${state.currentUser.id}/default-list`);
        state.currentListId = data.id;

        // 既存リストをクリアして読み込み
        state.listItems = [];
        renderListTable();

        for (const item of data.items) {
            const entry = { symbol: item.symbol, loading: true, tags: item.tags || [] };
            state.listItems.push(entry);
            renderListTable();
            loadListItemData(item.symbol, item.tags);
        }
    } catch (err) {
        console.warn('Default list load failed:', err);
    }
}

async function loadListItemData(symbol, tags) {
    try {
        const [price, profile, indicators, memo] = await Promise.all([
            fetchAPI(`/stock/${symbol}`),
            fetchAPI(`/stock/${symbol}/profile`),
            fetchAPI(`/stock/${symbol}/indicators`),
            fetchStockMemo(symbol),
        ]);

        const idx = state.listItems.findIndex(i => i.symbol === symbol);
        if (idx >= 0) {
            state.listItems[idx] = {
                symbol,
                loading: false,
                name: profile.name,
                country: profile.country,
                price: price.price,
                currency: profile.currency,
                market_cap: profile.market_cap,
                mix_index: indicators.mix_index,
                dividend_yield: indicators.dividend_yield,
                tags: tags || [],
                memo,
            };
            renderListTable();
        }
    } catch (err) {
        const idx = state.listItems.findIndex(i => i.symbol === symbol);
        if (idx >= 0) {
            state.listItems[idx].loading = false;
            state.listItems[idx].name = '(取得失敗)';
            renderListTable();
        }
    }
}

async function fetchStockMemo(symbol) {
    if (!state.currentUser) return null;
    try {
        const data = await fetchAPI(`/user/${state.currentUser.id}/memo/${symbol}`);
        return data.memo || null;
    } catch (err) {
        console.warn('Memo fetch failed:', err);
        return null;
    }
}

export async function editStockMemo(symbol) {
    if (!state.currentUser) {
        showToast('メモ機能はログインが必要です', 'warning');
        return;
    }

    const item = state.listItems.find(i => i.symbol === symbol);
    const currentMemo = item?.memo || '';
    const input = window.prompt(`${symbol} のメモを入力`, currentMemo);
    if (input === null) return;

    const memo = input.trim() === '' ? null : input.trim();

    try {
        const saved = await fetchAPI(`/user/${state.currentUser.id}/memo/${symbol}`, {
            method: 'PUT',
            body: JSON.stringify({ memo }),
        });
        if (item) {
            item.memo = saved.memo || null;
            renderListTable();
        }
        showToast('メモを保存しました', 'success');
    } catch (err) {
        showToast(`メモ保存に失敗しました: ${err.message}`, 'error');
    }
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

export function quickAddToList(symbol) {
    openListAddModal(symbol);
}

export function openListAddModal(prefill = '') {
    const modal = document.getElementById('list-add-modal-overlay');
    const input = document.getElementById('list-add-modal-input');
    input.value = (prefill || '').toUpperCase();
    modal.classList.remove('hidden');
    setTimeout(() => input.focus(), 30);
}

export function closeListAddModal() {
    document.getElementById('list-add-modal-overlay').classList.add('hidden');
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

    const item = { symbol, loading: true, tags: [] };
    state.listItems.push(item);
    renderListTable();

    // サーバーにも追加（ログイン中のみ）
    if (state.currentUser && state.currentListId) {
        try {
            await fetchAPI(`/user/${state.currentUser.id}/lists/${state.currentListId}/items`, {
                method: 'POST',
                body: JSON.stringify({ symbol, tags: [] }),
            });
        } catch (err) {
            console.warn('Server add failed:', err);
        }
    }

    try {
        const [price, profile, indicators, memo] = await Promise.all([
            fetchAPI(`/stock/${symbol}`),
            fetchAPI(`/stock/${symbol}/profile`),
            fetchAPI(`/stock/${symbol}/indicators`),
            fetchStockMemo(symbol),
        ]);

        const idx = state.listItems.findIndex(i => i.symbol === symbol);
        if (idx >= 0) {
            state.listItems[idx] = {
                symbol,
                loading: false,
                name: profile.name,
                country: profile.country,
                price: price.price,
                currency: profile.currency,
                market_cap: profile.market_cap,
                mix_index: indicators.mix_index,
                dividend_yield: indicators.dividend_yield,
                tags: state.listItems[idx].tags || [],
                memo,
            };
            renderListTable();
            return true;
        }
    } catch (err) {
        state.listItems = state.listItems.filter(i => i.symbol !== symbol);
        renderListTable();
        // サーバーからも削除
        if (state.currentUser && state.currentListId) {
            try {
            await fetchAPI(`/user/${state.currentUser.id}/lists/${state.currentListId}/items/${symbol}`, { method: 'DELETE' });
            } catch { /* ignore */ }
        }
        showToast(`${symbol} の取得に失敗しました`, 'error');
        return false;
    }

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

    // サーバーからも削除
    if (state.currentUser && state.currentListId) {
        try {
            await fetchAPI(`/user/${state.currentUser.id}/lists/${state.currentListId}/items/${symbol}`, { method: 'DELETE' });
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
        tagEl.innerHTML = `<span class="tag-chip small" style="--tag-hue:${color}">${escapeHtml(state.activeTagFilter)}</span>`;
    } else {
        filterBar.classList.add('hidden');
    }
}

export function renderListTable() {
    const emptyEl = document.getElementById('list-empty');
    const tableEl = document.getElementById('list-table-section');

    if (state.listItems.length === 0) {
        emptyEl.classList.remove('hidden');
        tableEl.classList.add('hidden');
        updateFilterButtonState();
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
            arrow.textContent = state.sortAsc ? ' ▲' : ' ▼';
        } else {
            arrow.textContent = '';
        }
    });

    const tbody = document.getElementById('comparison-tbody');
    if (sorted.length === 0) {
        tbody.innerHTML = '<tr class="row-loading"><td colspan="9" style="color:var(--text-muted)">検索条件に一致する銘柄がありません</td></tr>';
        return;
    }

    let html = '';

    sorted.forEach(item => {
        if (item.loading) {
            const isPendingDelete = state.pendingDeleteSymbol === item.symbol;
            html += `
                <tr class="row-loading" data-symbol="${item.symbol}">
                    <td class="table-ticker">${item.symbol}</td>
                    <td colspan="7" style="color:var(--text-muted)">読み込み中...</td>
                    <td><button class="btn-remove ${isPendingDelete ? 'confirm' : ''}" type="button" data-action="remove-item" data-symbol="${item.symbol}">${isPendingDelete ? 'OK?' : '削除'}</button></td>
                </tr>`;
            return;
        }

        let tagsHtml = '';
        if (item.tags && item.tags.length > 0) {
            item.tags.forEach(tag => {
                const color = getTagColor(tag);
                tagsHtml += `<span class="tag-chip small" style="--tag-hue:${color}">${escapeHtml(tag)}</span>`;
            });
        }
        tagsHtml += `<button class="btn-tag-edit" type="button" data-action="open-tag-modal" data-symbol="${item.symbol}" title="タグ編集">🏷️</button>`;

        const memoPreview = item.memo
            ? escapeHtml(item.memo.length > 24 ? `${item.memo.slice(0, 24)}...` : item.memo)
            : '<span class="memo-empty">未入力</span>';
        const memoTitle = item.memo ? escapeAttr(item.memo) : '';
        const marketCapCategory = getMarketCapCategory(item.market_cap, item.currency);
        const mixRating = getMixIndexRating(item.mix_index);
        const mixBadge = mixRating ? `<span class="indicator-badge ${mixRating}">${getRatingSymbol(mixRating)}</span>` : '';
        const isPendingDelete = state.pendingDeleteSymbol === item.symbol;

        html += `
            <tr data-symbol="${item.symbol}">
                <td class="table-ticker">${item.symbol}</td>
                <td class="table-company">${escapeHtml(item.name || '—')}</td>
                <td class="numeric">${item.price != null ? formatCurrency(item.price, item.currency) : '—'}</td>
                <td class="table-market-cap" title="${item.market_cap != null ? escapeAttr(formatLargeNumber(item.market_cap, item.currency)) : ''}">${marketCapCategory || '—'}</td>
                <td class="numeric">${item.mix_index != null ? item.mix_index.toFixed(2) : '—'} ${mixBadge}</td>
                <td class="numeric">${item.dividend_yield != null ? item.dividend_yield.toFixed(2) + '%' : '—'}</td>
                <td class="table-tags">${tagsHtml}</td>
                <td class="table-memo"><button class="btn-memo" type="button" data-action="edit-memo" data-symbol="${item.symbol}">📝</button><span class="memo-preview" title="${memoTitle}">${memoPreview}</span></td>
                <td><button class="btn-remove ${isPendingDelete ? 'confirm' : ''}" type="button" data-action="remove-item" data-symbol="${item.symbol}">${isPendingDelete ? 'OK?' : '削除'}</button></td>
            </tr>`;
    });

    tbody.innerHTML = html;
}

export function goToDetail(symbol) {
    document.getElementById('search-input').value = symbol;
    window.switchView('detail');
    searchStock();
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
            html += `<button class="filter-tag-item ${isActive ? 'active' : ''}" type="button" data-action="select-filter-tag" data-tag="${escapeAttr(tag)}" style="--tag-hue:${color}">
                <span class="filter-tag-name">${escapeHtml(tag)}</span>
                <span class="filter-tag-count">${count}件</span>
            </button>`;
        });
        html += '</div>';
        container.innerHTML = html;
    }

    document.getElementById('filter-modal-overlay').classList.remove('hidden');
}

export function closeFilterModal() {
    document.getElementById('filter-modal-overlay').classList.add('hidden');
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
export function openTagModal(symbol) {
    state.tagEditSymbol = symbol;
    document.getElementById('tag-modal-symbol').textContent = symbol;
    document.getElementById('tag-input').value = '';
    renderCurrentTags();
    document.getElementById('tag-modal-overlay').classList.remove('hidden');
}

export function closeTagModal() {
    document.getElementById('tag-modal-overlay').classList.add('hidden');
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
        html += `<span class="tag-chip editable" style="--tag-hue:${color}">
            ${escapeHtml(tag)}
            <button class="tag-remove-btn" type="button" data-action="remove-tag" data-tag="${escapeAttr(tag)}" title="削除">✕</button>
        </span>`;
    });
    container.innerHTML = html;
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
        await fetchAPI(`/user/${state.currentUser.id}/lists/${state.currentListId}/items/${symbol}/tags`, {
            method: 'PUT',
            body: JSON.stringify({ tags }),
        });
    } catch (err) {
        console.warn('Tag sync failed:', err);
    }
}

function getTagColor(tag) {
    let hash = 0;
    for (let i = 0; i < tag.length; i++) {
        hash = tag.charCodeAt(i) + ((hash << 5) - hash);
    }
    return Math.abs(hash % 360);
}

export function setListSearchQuery(query) {
    state.listSearchQuery = query;
    renderListTable();
}

export function bindListEvents() {
    document.getElementById('list-add-btn')?.addEventListener('click', () => openListAddModal());
    document.getElementById('list-add-modal-submit')?.addEventListener('click', submitListAddFromModal);
    document.getElementById('active-filter-clear')?.addEventListener('click', clearTagFilter);

    document.querySelectorAll('#comparison-table th.sortable[data-sort]').forEach(th => {
        th.addEventListener('click', () => {
            const key = th.dataset.sort;
            if (key) sortTable(key);
        });
    });

    const listAddOverlay = document.getElementById('list-add-modal-overlay');
    listAddOverlay?.addEventListener('click', e => {
        if (e.target === listAddOverlay) closeListAddModal();
    });
    document.getElementById('list-add-modal-close')?.addEventListener('click', closeListAddModal);

    const filterOverlay = document.getElementById('filter-modal-overlay');
    filterOverlay?.addEventListener('click', e => {
        if (e.target === filterOverlay) closeFilterModal();
    });
    document.getElementById('filter-modal-close')?.addEventListener('click', closeFilterModal);
    document.getElementById('filter-clear-btn')?.addEventListener('click', () => {
        clearTagFilter();
        closeFilterModal();
    });

    const tagOverlay = document.getElementById('tag-modal-overlay');
    tagOverlay?.addEventListener('click', e => {
        if (e.target === tagOverlay) closeTagModal();
    });
    document.getElementById('tag-modal-close')?.addEventListener('click', closeTagModal);
    document.getElementById('tag-add-btn')?.addEventListener('click', addTagFromInput);

    document.addEventListener('click', e => {
        const quickAddEl = e.target.closest('[data-quick-add]');
        if (quickAddEl) {
            quickAddToList(quickAddEl.dataset.quickAdd);
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
                openTagModal(symbol);
                return;
            }
            if (action === 'edit-memo' && symbol) {
                editStockMemo(symbol);
                return;
            }
            if (action === 'remove-tag' && tag) {
                removeTag(tag);
                return;
            }
        }

        const row = e.target.closest('#comparison-tbody tr[data-symbol]');
        if (row && !e.target.closest('button')) {
            const symbol = row.dataset.symbol;
            if (symbol) goToDetail(symbol);
        }
    });
}
