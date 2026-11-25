import { downloadAPI, fetchAPI } from './api.js';
import { createActionModal } from './action-modal.js';
import { logoutUser, storeUserSession } from './auth.js';
import { loadDefaultList, resetListSession } from './list.js';
import { state } from './state.js';

const byId = id => document.getElementById(id);
const importReplaceModal = createActionModal('import-replace');

function feedback(id, message = '') {
    const element = byId(id);
    element.textContent = message;
    element.classList.toggle('hidden', !message);
}

export function openAccountSettings() {
    if (!state.currentUser) return;
    byId('account-menu').classList.add('hidden');
    byId('account-settings-overlay').classList.remove('hidden');
    byId('account-current-password').focus();
}

export function closeAccountSettings() {
    importReplaceModal.close();
    byId('account-settings-overlay').classList.add('hidden');
    for (const id of ['account-current-password', 'account-new-password', 'account-confirm-password',
        'account-delete-username', 'account-delete-password', 'account-import-file']) {
        byId(id).value = '';
    }
    byId('account-import-mode').value = 'merge';
    for (const id of ['account-password-error', 'account-delete-error', 'account-data-error',
        'account-logout-error', 'account-password-status', 'account-import-result']) feedback(id);
}

async function accountAction(event, errorId, action) {
    event?.preventDefault();
    const user = state.currentUser;
    if (!user) return;
    const fields = byId('account-settings-fields');
    if (fields.disabled) return;
    fields.disabled = true;
    if (errorId) feedback(errorId);
    try {
        await action(user, `/user/${encodeURIComponent(user.id)}`);
    } catch (error) {
        if (!errorId) throw error;
        if (state.currentUser === user) feedback(errorId, error.message);
    } finally {
        fields.disabled = false;
    }
}

export function submitPasswordChange(event) {
    return accountAction(event, 'account-password-error', async (_user, path) => {
        feedback('account-password-status');
        const current_password = byId('account-current-password').value;
        const new_password = byId('account-new-password').value;
        if (new_password !== byId('account-confirm-password').value) {
            throw new Error('新しいパスワードが一致しません');
        }
        const updated = await fetchAPI(`${path}/password`, {
            method: 'POST', body: JSON.stringify({ current_password, new_password }),
        });
        storeUserSession(updated);
        for (const id of ['account-current-password', 'account-new-password', 'account-confirm-password']) byId(id).value = '';
        feedback('account-password-status', 'パスワードを変更しました');
    });
}

export function submitAccountDeletion(event) {
    return accountAction(event, 'account-delete-error', async (user, path) => {
        if (byId('account-delete-username').value !== user.username) throw new Error('ユーザー名が一致しません');
        await fetchAPI(path, {
            method: 'DELETE', body: JSON.stringify({ current_password: byId('account-delete-password').value }),
        });
        logoutUser();
    });
}

export function renderImportResult(result) {
    const { lists, items, tags, memos } = result.imported;
    const lines = [
        `取り込み結果　リスト ${lists} 件、銘柄 ${items} 件、タグ ${tags} 件、メモ ${memos} 件`,
        `スキップ ${result.skipped.length} 件`,
        ...result.skipped.map(({ entry, reason }) => `${entry} — ${reason}`),
    ];
    feedback('account-import-result', lines.join('\n'));
}

function requestImport(event) {
    event.preventDefault();
    const user = state.currentUser;
    if (!user || byId('account-settings-fields').disabled) return;
    feedback('account-import-result');
    feedback('account-data-error');
    const input = byId('account-import-file');
    const file = input.files?.[0];
    if (!file) {
        feedback('account-data-error', 'JSONファイルを選択してください');
        return;
    }
    const mode = byId('account-import-mode').value;
    const performImport = async (_user, path) => {
        const result = await fetchAPI(`${path}/import?mode=${encodeURIComponent(mode)}`, { method: 'POST', body: file });
        input.value = '';
        renderImportResult(result);
        if (mode === 'replace') resetListSession();
        await loadDefaultList(mode === 'replace' ? '' : state.currentListId);
    };
    if (mode !== 'replace') return accountAction(null, 'account-data-error', performImport);

    const settings = byId('account-settings-overlay');
    settings.classList.add('hidden');
    importReplaceModal.open({
        focus: byId('import-replace-message'),
        opener: byId('account-import-submit'),
        onClose() {
            if (state.currentUser === user) settings.classList.remove('hidden');
        },
        onSubmit() {
            if (state.currentUser === user) return accountAction(null, null, performImport);
        },
    });
}

export function bindAccountSettingsEvents() {
    importReplaceModal.bind();
    const overlay = byId('account-settings-overlay');
    byId('account-menu-settings').addEventListener('click', openAccountSettings);
    byId('account-settings-close').addEventListener('click', closeAccountSettings);
    overlay.addEventListener('click', event => {
        if (event.target === overlay) closeAccountSettings();
    });
    byId('account-password-form').addEventListener('submit', submitPasswordChange);
    byId('account-delete-form').addEventListener('submit', submitAccountDeletion);
    byId('account-import-form').addEventListener('submit', requestImport);
    byId('account-logout-all').addEventListener('click', event =>
        accountAction(event, 'account-logout-error', async (_user, path) => {
            await fetchAPI(`${path}/logout-all`, { method: 'POST' });
            logoutUser();
        }));
    byId('account-export').addEventListener('click', event =>
        accountAction(event, 'account-data-error', (_user, path) =>
            downloadAPI(`${path}/export`, `watchlists-${new Date().toISOString().slice(0, 10)}.json`)));
}
