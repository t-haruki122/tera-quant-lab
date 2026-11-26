/**
 * Stock Analyzer — Account and auth modals
 */

import { state, cache } from './state.js';
import { bindAccountSettingsEvents, closeAccountSettings } from './account.js';
import { fetchAPI } from './api.js';
import { showToast } from './utils.js';
import { loadDefaultList, resetListSession } from './list.js';
import { createDialog } from './dialog.js';

const authDialog = createDialog('auth-modal-overlay');
const byId = id => document.getElementById(id);

function menuItems() {
    return [...byId('account-menu').querySelectorAll('[role="menuitem"]')]
        .filter(item => !item.disabled && item.getClientRects().length);
}

function openAccountMenu(last = false) {
    byId('account-menu').classList.remove('hidden');
    byId('account-btn').setAttribute('aria-expanded', 'true');
    menuItems().at(last ? -1 : 0)?.focus();
}

export function closeAccountMenu(restoreFocus = false) {
    byId('account-menu').classList.add('hidden');
    const trigger = byId('account-btn');
    trigger.setAttribute('aria-expanded', 'false');
    if (restoreFocus) trigger.focus();
}

export function toggleAccountMenu() {
    if (byId('account-menu').classList.contains('hidden')) openAccountMenu();
    else closeAccountMenu(true);
}

export function openStatsFromAccountMenu() {
    closeAccountMenu(true);
    window.switchView('stats');
}

function handleMenuKey(event) {
    const items = menuItems();
    const index = items.indexOf(document.activeElement);
    const next = {
        ArrowDown: (index + 1) % items.length,
        ArrowUp: (index > 0 ? index - 1 : items.length - 1),
        Home: 0,
        End: items.length - 1,
    }[event.key];
    if (event.key === 'Escape' || event.key === 'Tab') {
        if (event.key === 'Escape') event.preventDefault();
        closeAccountMenu(true);
    } else if (Number.isInteger(next)) {
        event.preventDefault();
        items[next]?.focus();
    }
}

export function updateAccountUI() {
    const btnLabel = document.getElementById('account-btn-label');
    const menuUser = document.getElementById('account-menu-user');
    const menuGuest = document.getElementById('account-menu-guest');
    const menuLogout = document.getElementById('account-menu-logout');
    const menuSettings = document.getElementById('account-menu-settings');
    const accountBtn = document.getElementById('account-btn');

    document.getElementById('list-description').textContent = state.currentUser
        ? '株数と現金を入力すると、評価額と構成比を表示します。'
        : 'ログインすると、株数や現金を入力して評価額と構成比を確認できます。';
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
    accountBtn.setAttribute('aria-label', `${btnLabel.textContent}、ユーザーメニュー`);
    closeAccountMenu();
    menuSettings.classList.toggle('hidden', !state.currentUser);
    document.dispatchEvent?.(new Event('accountchange'));
}

// ===== Auth Modal =====
export function openAuthModal(mode, opener) {
    state.authMode = mode;
    closeAccountMenu();
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

    authDialog.open(byId('auth-username'), { opener, fallbackFocus: byId('account-btn') });
}

export function closeAuthModal() {
    authDialog.close();
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

    if (state.authMode === 'register' && password.length < 8) {
        errorEl.textContent = 'パスワードは8文字以上にしてください';
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

        storeUserSession(user);
        resetListSession();
        closeAuthModal();
        showToast(state.authMode === 'login' ? 'ログインしました' : 'アカウントを作成しました', 'success');

        await loadDefaultList();
    } catch (err) {
        errorEl.textContent = err.message;
        errorEl.classList.remove('hidden');
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = state.authMode === 'login' ? 'ログイン' : '登録';
    }
}

export function storeUserSession(user) {
    cache.clear();
    state.currentUser = user;
    try { localStorage.setItem('stockAnalyzerUser', JSON.stringify(user)); } catch { /* storage unavailable */ }
    updateAccountUI();
}

export function logoutUser() {
    cache.clear();
    state.currentUser = null;
    try { localStorage.removeItem('stockAnalyzerUser'); } catch { /* storage unavailable */ }
    resetListSession();
    closeAccountSettings();
    updateAccountUI();
    showToast('ログアウトしました', 'info');
}

export function bindAuthEvents() {
    bindAccountSettingsEvents();
    const accountBtn = document.getElementById('account-btn');
    const accountMenu = document.getElementById('account-menu');
    const accountGuest = document.getElementById('account-menu-guest');
    const accountStatsBtn = document.getElementById('account-menu-stats');
    const accountLogoutBtn = document.getElementById('account-menu-logout');
    const authCloseBtn = document.getElementById('auth-modal-close');
    const authSubmitBtn = document.getElementById('auth-submit-btn');
    const authToggleLink = document.getElementById('auth-toggle-link');

    accountBtn?.addEventListener('click', toggleAccountMenu);
    accountBtn?.addEventListener('keydown', event => {
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            openAccountMenu(event.key === 'ArrowUp');
        }
    });
    accountMenu?.addEventListener('keydown', handleMenuKey);
    document.addEventListener('click', event => {
        if (!event.target.closest('.account-section') && !accountMenu.classList.contains('hidden')) closeAccountMenu();
    });

    accountGuest?.addEventListener('click', e => {
        const btn = e.target.closest('[data-auth-mode]');
        if (!btn) return;
        openAuthModal(btn.dataset.authMode, accountBtn);
    });

    accountStatsBtn?.addEventListener('click', openStatsFromAccountMenu);
    accountLogoutBtn?.addEventListener('click', () => {
        closeAccountMenu(true);
        logoutUser();
    });

    authDialog.bind(closeAuthModal);
    authCloseBtn?.addEventListener('click', closeAuthModal);
    authSubmitBtn?.addEventListener('click', submitAuth);
    authToggleLink?.addEventListener('click', toggleAuthMode);
}
