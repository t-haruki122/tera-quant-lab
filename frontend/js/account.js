import { downloadAPI, fetchAPI } from './api.js';
import { createActionModal } from './action-modal.js';
import { closeAccountMenu, logoutUser, storeUserSession } from './auth.js';
import { createDialog } from './dialog.js';
import { loadDefaultList, resetListSession } from './list.js';
import { state } from './state.js';
import { formatTimestamp, showToast } from './utils.js';

const byId = id => document.getElementById(id);
const importReplaceModal = createActionModal('import-replace');
const revokeTokenModal = createActionModal('revoke-token');
const settingsDialog = createDialog('account-settings-overlay');
let tokenListRequest = 0;
let tokenPanelSession = 0;
let createdTokenId = null;

function feedback(id, message = '') {
    const element = byId(id);
    element.textContent = message;
    element.classList.toggle('hidden', !message);
}

export function openAccountSettings() {
    if (!state.currentUser) return;
    closeAccountMenu();
    settingsDialog.open(byId('account-current-password'), { opener: byId('account-btn') });
    return loadTokens();
}

export function closeAccountSettings() {
    importReplaceModal.close();
    revokeTokenModal.close();
    settingsDialog.close();
    tokenPanelSession++;
    tokenListRequest++;
    clearCreatedToken();
    byId('account-token-list').replaceChildren();
    byId('account-token-empty').classList.add('hidden');
    byId('account-token-name').value = 'Claude Code';
    byId('account-token-expiry').value = '90';
    for (const id of ['account-current-password', 'account-new-password', 'account-confirm-password',
        'account-delete-username', 'account-delete-password', 'account-import-file']) {
        byId(id).value = '';
    }
    byId('account-import-mode').value = 'merge';
    for (const id of ['account-password-error', 'account-delete-error', 'account-data-error',
        'account-logout-error', 'account-password-status', 'account-import-result', 'account-token-error']) feedback(id);
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
        return await action(user, `/user/${encodeURIComponent(user.id)}`);
    } catch (error) {
        if (!errorId) throw error;
        if (state.currentUser === user) feedback(errorId, error.message);
    } finally {
        fields.disabled = false;
    }
}

function tokensPath(user) {
    return `/user/${encodeURIComponent(user.id)}/api-tokens`;
}

function clearCreatedToken() {
    createdTokenId = null;
    for (const id of ['account-token-value', 'account-token-command', 'account-token-prompt']) byId(id).value = '';
    byId('account-token-once').classList.add('hidden');
    byId('account-token-prompt-fallback').classList.add('hidden');
    byId('account-token-status').textContent = '';
    byId('account-token-create').disabled = false;
}

async function loadTokens() {
    const user = state.currentUser;
    const request = ++tokenListRequest;
    feedback('account-token-error');
    try {
        const tokens = await fetchAPI(tokensPath(user), { cache: 'no-store' });
        if (request !== tokenListRequest || state.currentUser !== user) return;
        byId('account-token-list').replaceChildren(...tokens.map(renderToken));
        byId('account-token-empty').classList.toggle('hidden', tokens.length > 0);
    } catch (error) {
        if (request === tokenListRequest && state.currentUser === user) feedback('account-token-error', error.message);
    }
}

function renderToken(token) {
    const element = (tag, text = '') => Object.assign(document.createElement(tag), { textContent: text });
    const item = element('li');
    const identity = element('p');
    identity.appendChild(element('strong', token.name));
    identity.appendChild(element('code', ` ${token.token_prefix}…`));
    if (token.expired) {
        const badge = element('span', '期限切れ');
        badge.className = 'account-token-expired';
        identity.appendChild(badge);
    }
    const dates = element('dl');
    for (const [label, value, blank] of [
        ['作成日', token.created_at, '—'], ['最終使用', token.last_used_at, '未使用'], ['有効期限', token.expires_at, 'なし'],
    ]) {
        dates.appendChild(element('dt', label));
        dates.appendChild(element('dd', value ? formatTimestamp(value) : blank));
    }
    const revoke = element('button', '無効化');
    revoke.type = 'button';
    revoke.className = 'account-action account-action-danger';
    revoke.setAttribute('aria-label', `「${token.name}」 ${token.token_prefix}… を無効化`);
    revoke.addEventListener('click', () => requestTokenRevocation(token, revoke));
    item.appendChild(identity);
    item.appendChild(dates);
    item.appendChild(revoke);
    return item;
}

function requestTokenRevocation(token, opener) {
    const user = state.currentUser;
    const session = tokenPanelSession;
    const settings = byId('account-settings-overlay');
    const message = byId('revoke-token-message');
    message.textContent = `「${token.name}」（${token.token_prefix}…）を無効化しますか？`;
    settings.classList.add('hidden');
    revokeTokenModal.open({
        focus: message, opener, fallbackFocus: byId('account-token-name'),
        onClose() {
            if (state.currentUser === user) settings.classList.remove('hidden');
        },
        onSubmit() {
            return accountAction(null, null, async activeUser => {
                if (activeUser !== user) return;
                await fetchAPI(`${tokensPath(user)}/${encodeURIComponent(token.id)}`, { method: 'DELETE' });
                if (session !== tokenPanelSession) return;
                if (createdTokenId === token.id) clearCreatedToken();
                await loadTokens();
                showToast('APIトークンを無効化しました', 'success');
            });
        },
    });
}

async function createToken(event) {
    const session = tokenPanelSession;
    const created = await accountAction(event, 'account-token-error', async user => {
        if (byId('account-token-create').disabled) return;
        const name = byId('account-token-name').value.trim();
        const expires_in_days = Number(byId('account-token-expiry').value) || null;
        const { id, token } = await fetchAPI(tokensPath(user), {
            method: 'POST', cache: 'no-store', body: JSON.stringify({ name, expires_in_days }),
        });
        if (session !== tokenPanelSession) return;
        createdTokenId = id;
        byId('account-token-value').value = token;
        byId('account-token-command').value = `claude mcp add --transport http --scope user tera-quant-lab ${window.location.origin}/mcp --header "Authorization: Bearer ${token}"`;
        byId('account-token-once').classList.remove('hidden');
        byId('account-token-create').disabled = true;
        byId('account-token-status').textContent = 'APIトークンを作成しました。閉じる前にコピーしてください。';
        return true;
    });
    if (created && session === tokenPanelSession && createdTokenId !== null) {
        byId('account-token-command-copy').focus();
        await loadTokens();
    }
}

async function copyTokenText(id, prompt = false) {
    const field = byId(id);
    const value = prompt
        ? `次のコマンドを実行して、tera-quant-lab の MCP サーバーを設定してください。\n${byId('account-token-command').value}`
        : field.value;
    const session = tokenPanelSession;
    try {
        if (window.isSecureContext && navigator.clipboard?.writeText) {
            await navigator.clipboard.writeText(value);
            showToast('コピーしました', 'success');
            return;
        }
    } catch {
        // A browser may deny clipboard access even on HTTPS.
    }
    if (session !== tokenPanelSession || byId('account-token-once').classList.contains('hidden')) return;
    if (prompt) {
        field.value = value;
        byId('account-token-prompt-fallback').classList.remove('hidden');
    }
    field.focus();
    field.select();
    showToast('自動でコピーできませんでした。選択したテキストをコピーしてください。', 'warning');
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
    const { lists, items, tags, memos, holdings = 0, alerts = 0, backtest_runs = 0 } = result.imported;
    const lines = [
        `取り込み結果　リスト ${lists} 件、銘柄 ${items} 件、タグ ${tags} 件、メモ ${memos} 件`,
        `保有銘柄 ${holdings} 件、価格アラート ${alerts} 件`,
        `保存済みバックテスト ${backtest_runs} 件`,
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
        document.dispatchEvent(new Event('investmentchange'));
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
    revokeTokenModal.bind();
    byId('account-menu-settings').addEventListener('click', openAccountSettings);
    byId('account-settings-close').addEventListener('click', closeAccountSettings);
    settingsDialog.bind(closeAccountSettings);
    byId('account-password-form').addEventListener('submit', submitPasswordChange);
    byId('account-delete-form').addEventListener('submit', submitAccountDeletion);
    byId('account-import-form').addEventListener('submit', requestImport);
    byId('account-token-form').addEventListener('submit', createToken);
    byId('account-token-copy').addEventListener('click', () => copyTokenText('account-token-value'));
    byId('account-token-command-copy').addEventListener('click', () => copyTokenText('account-token-command'));
    byId('account-token-prompt-copy').addEventListener('click', () => copyTokenText('account-token-prompt', true));
    byId('account-token-dismiss').addEventListener('click', () => {
        clearCreatedToken();
        byId('account-token-create').focus();
    });
    const clearTokenOnNavigation = () => {
        tokenPanelSession++;
        clearCreatedToken();
    };
    window.addEventListener?.('pagehide', clearTokenOnNavigation);
    window.addEventListener?.('hashchange', clearTokenOnNavigation);
    byId('account-logout-all').addEventListener('click', event =>
        accountAction(event, 'account-logout-error', async (_user, path) => {
            await fetchAPI(`${path}/logout-all`, { method: 'POST' });
            logoutUser();
        }));
    byId('account-export').addEventListener('click', event =>
        accountAction(event, 'account-data-error', (_user, path) =>
            downloadAPI(`${path}/export`, `watchlists-${new Date().toISOString().slice(0, 10)}.json`)));
}
