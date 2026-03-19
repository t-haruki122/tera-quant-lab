/**
 * Stock Analyzer — メインアプリケーションロジック
 */

import { state } from './js/state.js';
import { fetchAPI } from './js/api.js';
import { fetchStats } from './js/stats.js';
import { updateAccountUI, submitAuth } from './js/auth.js';
import { loadDefaultList, submitListAddFromModal, addTagFromInput, setListSearchQuery } from './js/list.js';
import { searchStock } from './js/detail.js';

// Register side-effect modules so their window bindings are active for inline handlers.
import './js/utils.js';
import './js/detail.js';
import './js/chart.js';
import './js/financial-chart.js';
import './js/auth.js';
import './js/list.js';

export function switchView(view) {
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    const listBtn = document.getElementById('btn-list-view');
    if (listBtn) listBtn.classList.remove('active');

    if (view === 'detail') {
        document.getElementById('detail-view').classList.add('active');
    } else if (view === 'stats') {
        document.getElementById('stats-view').classList.add('active');
        fetchStats();
    } else {
        document.getElementById('list-view').classList.add('active');
        if (listBtn) listBtn.classList.add('active');
    }
}

Object.assign(window, {
    switchView,
});

export async function fetchExchangeRate() {
    try {
        const res = await fetchAPI('/forex/usdjpy', { method: 'GET' });
        state.exchangeRateUSDJPY = res.rate;
    } catch (err) {
        console.warn('Failed to fetch exchange rate:', err);
    }
}

// ===== Init =====
(async function init() {
    const saved = localStorage.getItem('stockAnalyzerUser');
    if (saved) {
        try {
            state.currentUser = JSON.parse(saved);
            updateAccountUI();
            // ログイン済みなら自動でリスト読み込み
            loadDefaultList();
        } catch { /* ignore */ }
    }
    
    // 為替レートの初期取得
    await fetchExchangeRate();
    
    // 統計情報の定期取得を開始
    fetchStats();
    setInterval(fetchStats, 5000);
})();

// ===== Event Listeners =====
document.getElementById('search-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') searchStock();
});

document.getElementById('list-search-input').addEventListener('input', e => {
    setListSearchQuery(e.target.value);
});

document.getElementById('list-add-modal-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') submitListAddFromModal();
});

document.getElementById('tag-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') addTagFromInput();
});

document.getElementById('auth-password').addEventListener('keydown', e => {
    if (e.key === 'Enter') submitAuth();
});
