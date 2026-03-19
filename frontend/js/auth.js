/**
 * Stock Analyzer — Account and auth modals
 */

import { state } from './state.js';
import { fetchAPI } from './api.js';
import { showToast } from './utils.js';
import { loadDefaultList, renderListTable } from './list.js';

// ===== Account Management =====
export function toggleAccountMenu() {
    const menu = document.getElementById('account-menu');
    menu.classList.toggle('hidden');

    if (!menu.classList.contains('hidden')) {
        setTimeout(() => {
            document.addEventListener('click', closeAccountMenuOnOutside, { once: true });
        }, 10);
    }
}

export function openStatsFromAccountMenu() {
    document.getElementById('account-menu').classList.add('hidden');
    window.switchView('stats');
}

function closeAccountMenuOnOutside(e) {
    const section = document.querySelector('.account-section');
    if (!section.contains(e.target)) {
        document.getElementById('account-menu').classList.add('hidden');
    }
}

export function updateAccountUI() {
    const btnLabel = document.getElementById('account-btn-label');
    const menu = document.getElementById('account-menu');
    const menuUser = document.getElementById('account-menu-user');
    const menuGuest = document.getElementById('account-menu-guest');
    const menuLogout = document.getElementById('account-menu-logout');
    const accountBtn = document.getElementById('account-btn');

    if (state.currentUser) {
        btnLabel.textContent = state.currentUser.username;
        menuUser.classList.remove('hidden');
        menuGuest.classList.add('hidden');
        menuLogout.classList.remove('hidden');
        document.getElementById('account-menu-username').textContent = `👤 ${state.currentUser.username}`;
        accountBtn.classList.add('logged-in');
    } else {
        btnLabel.textContent = 'ログイン';
        menuUser.classList.add('hidden');
        menuGuest.classList.remove('hidden');
        menuLogout.classList.add('hidden');
        accountBtn.classList.remove('logged-in');
    }
    menu.classList.add('hidden');
}

// ===== Auth Modal =====
export function openAuthModal(mode) {
    state.authMode = mode;
    document.getElementById('account-menu').classList.add('hidden');
    document.getElementById('auth-modal-overlay').classList.remove('hidden');
    document.getElementById('auth-username').value = '';
    document.getElementById('auth-password').value = '';
    document.getElementById('auth-error').classList.add('hidden');

    if (mode === 'login') {
        document.getElementById('auth-modal-title').textContent = 'ログイン';
        document.getElementById('auth-submit-btn').textContent = 'ログイン';
        document.getElementById('auth-toggle-text').textContent = 'アカウントをお持ちでない方は';
        document.getElementById('auth-toggle-link').textContent = '新規登録';
    } else {
        document.getElementById('auth-modal-title').textContent = '新規登録';
        document.getElementById('auth-submit-btn').textContent = '登録';
        document.getElementById('auth-toggle-text').textContent = '既にアカウントをお持ちの方は';
        document.getElementById('auth-toggle-link').textContent = 'ログイン';
    }

    setTimeout(() => document.getElementById('auth-username').focus(), 100);
}

export function closeAuthModal() {
    document.getElementById('auth-modal-overlay').classList.add('hidden');
}

export function toggleAuthMode(e) {
    e.preventDefault();
    openAuthModal(state.authMode === 'login' ? 'register' : 'login');
}

export async function submitAuth() {
    const username = document.getElementById('auth-username').value.trim();
    const password = document.getElementById('auth-password').value;
    const errorEl = document.getElementById('auth-error');

    if (!username || !password) {
        errorEl.textContent = 'ユーザー名とパスワードを入力してください';
        errorEl.classList.remove('hidden');
        return;
    }

    const submitBtn = document.getElementById('auth-submit-btn');
    submitBtn.disabled = true;
    submitBtn.textContent = '処理中...';

    try {
        const endpoint = state.authMode === 'login' ? '/user/login' : '/user/register';
        const user = await fetchAPI(endpoint, {
            method: 'POST',
            body: JSON.stringify({ username, password }),
        });

        state.currentUser = user;
        localStorage.setItem('stockAnalyzerUser', JSON.stringify(user));
        updateAccountUI();
        closeAuthModal();
        showToast(state.authMode === 'login' ? 'ログインしました' : 'アカウントを作成しました', 'success');

        // ログイン/登録直後にデフォルトリストを読み込み
        await loadDefaultList();
    } catch (err) {
        errorEl.textContent = err.message;
        errorEl.classList.remove('hidden');
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = state.authMode === 'login' ? 'ログイン' : '登録';
    }
}

export function logoutUser() {
    state.currentUser = null;
    state.currentListId = null;
    state.listItems = [];
    state.activeTagFilter = null;
    localStorage.removeItem('stockAnalyzerUser');
    updateAccountUI();
    renderListTable();
    showToast('ログアウトしました', 'info');
}

Object.assign(window, {
    toggleAccountMenu,
    openStatsFromAccountMenu,
    openAuthModal,
    closeAuthModal,
    toggleAuthMode,
    submitAuth,
    logoutUser,
});
