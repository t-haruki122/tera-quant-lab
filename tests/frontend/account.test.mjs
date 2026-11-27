import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { elements, fromRepo, pressKey, storage } from './helpers/dom-shim.mjs';

const { cache, state } = await fromRepo('frontend/js/state.js');
const { bindAccountSettingsEvents, openAccountSettings, renderImportResult, submitAccountDeletion, submitPasswordChange }
    = await fromRepo('frontend/js/account.js');
const byId = id => document.getElementById(id);

function reset() {
    elements.clear();
    storage.clear();
    cache.clear();
    state.currentUser = { id: 'account/7', username: 'taro', access_token: 'before' };
    state.currentListId = 5;
    state.watchlists = [{ id: 5, name: 'My list' }];
    state.listItems = [{ symbol: 'AAPL' }];
    localStorage.setItem('stockAnalyzerUser', JSON.stringify(state.currentUser));
    globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => [] });
    openAccountSettings();
}

test('password mismatch displays inline, then a successful change stores the new token', async () => {
    reset();
    const calls = [];
    globalThis.fetch = async (url, options) => {
        calls.push([url, options.method, options.headers.get('Authorization'), JSON.parse(options.body)]);
        return { ok: true, status: 200, json: async () => ({ ...state.currentUser, access_token: 'after' }) };
    };
    byId('account-current-password').value = 'current-password';
    byId('account-new-password').value = 'new-password';
    byId('account-confirm-password').value = 'different';
    await submitPasswordChange();
    const error = byId('account-password-error');
    assert.equal(error.classList.contains('hidden'), false);
    assert.match(error.textContent, /一致しません/);
    assert.deepEqual(calls, []);

    byId('account-confirm-password').value = 'new-password';
    await submitPasswordChange();
    assert.deepEqual(calls, [['/user/account%2F7/password', 'POST', 'Bearer before', {
        current_password: 'current-password', new_password: 'new-password',
    }]]);
    assert.equal(state.currentUser.access_token, 'after');
    assert.equal(JSON.parse(storage.get('stockAnalyzerUser')).access_token, 'after');
    assert.equal(byId('account-current-password').value, '');
    assert.equal(error.classList.contains('hidden'), true);
    assert.match(byId('account-password-status').textContent, /変更しました/);
    assert.equal(byId('account-settings-overlay').classList.contains('hidden'), false);
});

test('account deletion requires the typed username and clears the session after 204', async () => {
    reset();
    const calls = [];
    globalThis.fetch = async (url, options) => {
        calls.push([url, options.method, JSON.parse(options.body)]);
        return { ok: true, status: 204, json: async () => assert.fail('204 has no JSON body') };
    };
    byId('account-delete-password').value = 'current-password';
    byId('account-delete-username').value = 'other';
    await submitAccountDeletion();
    assert.match(byId('account-delete-error').textContent, /ユーザー名が一致しません/);
    assert.deepEqual(calls, []);
    byId('account-delete-username').value = 'taro';
    await submitAccountDeletion();
    assert.deepEqual(calls, [['/user/account%2F7', 'DELETE', { current_password: 'current-password' }]]);
    assert.equal(state.currentUser, null);
    assert.equal(state.currentListId, null);
    assert.deepEqual(state.listItems, []);
    assert.equal(storage.has('stockAnalyzerUser'), false);
    assert.equal(byId('account-settings-overlay').classList.contains('hidden'), true);
});

test('logout-all clears the browser session after a successful response', async () => {
    reset();
    bindAccountSettingsEvents();
    let requested;
    globalThis.fetch = async (url, options) => {
        requested = [url, options.method];
        return { ok: true, status: 204 };
    };
    byId('account-logout-all').click();
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(requested, ['/user/account%2F7/logout-all', 'POST']);
    assert.equal(state.currentUser, null);
    assert.equal(storage.has('stockAnalyzerUser'), false);
});

test('import result shows counts and treats untrusted entries and reasons as text', () => {
    reset();
    const payload = '"><img src=x onerror=alert(1)>';
    renderImportResult({
        imported: { lists: 1, items: 2, tags: 3, memos: 4 },
        skipped: [{ entry: payload, reason: payload }],
    });
    const result = byId('account-import-result');
    assert.equal(result.classList.contains('hidden'), false);
    assert.match(result.textContent, /リスト 1 件、銘柄 2 件、タグ 3 件、メモ 4 件/);
    assert.match(result.textContent, /スキップ 1 件/);
    assert.ok(result.textContent.endsWith(`${payload} — ${payload}`));
    assert.equal(result.innerHTML, '');
});

test('replace import waits for confirmation, keeps errors inline, and returns to account settings', async () => {
    reset();
    bindAccountSettingsEvents();
    const file = { name: 'backup.json' };
    const form = byId('account-import-form');
    const input = byId('account-import-file');
    const overlay = byId('import-replace-overlay');
    const message = byId('import-replace-message');
    const settings = byId('account-settings-overlay');
    const tick = () => new Promise(resolve => setImmediate(resolve));
    const uploads = [];
    globalThis.fetch = async (url, options) => {
        if (options.method === 'POST') {
            uploads.push([url, options.body]);
            if (uploads.length === 1) {
                return { ok: false, status: 422, json: async () => ({ detail: 'バックアップの形式が正しくありません' }) };
            }
            return {
                ok: true, status: 200, json: async () => ({ imported: { lists: 1, items: 2, tags: 0, memos: 0 }, skipped: [] }),
            };
        }
        return {
            ok: true, status: 200, json: async () => url.endsWith('/lists')
                ? { lists: [{ id: 10, name: '取り込んだリスト' }], default_list_id: 10 }
                : { items: [] },
        };
    };

    form.requestSubmit();
    assert.match(byId('account-data-error').textContent, /JSONファイルを選択してください/);
    assert.equal(overlay.classList.contains('hidden'), true);
    input.files = [file];
    byId('account-import-mode').value = 'replace';
    form.requestSubmit();
    assert.equal(document.activeElement, message);
    assert.match(readFileSync('frontend/index.html', 'utf8'),
        /id="import-replace-message"[^>]*>リスト（保有株数・口数と現金を含む）、メモ、価格アラート、保存済みバックテストのうち、バックアップに含まれる種類のデータを置き換えますか？/);
    assert.equal(settings.classList.contains('hidden'), true);
    pressKey(overlay, message, 'Escape');
    assert.equal(overlay.classList.contains('hidden'), true);
    assert.equal(settings.classList.contains('hidden'), false);
    assert.equal(document.activeElement, byId('account-import-submit'));
    assert.deepEqual(uploads, []);

    form.requestSubmit();
    pressKey(overlay, message, 'Enter');
    await tick();
    assert.match(byId('import-replace-error').textContent, /バックアップの形式/);
    assert.equal(overlay.classList.contains('hidden'), false);
    pressKey(overlay, message, 'Enter');
    await tick();
    assert.deepEqual(uploads, [
        ['/user/account%2F7/import?mode=replace', file],
        ['/user/account%2F7/import?mode=replace', file],
    ]);
    assert.equal(overlay.classList.contains('hidden'), true);
    assert.equal(settings.classList.contains('hidden'), false);
    assert.match(byId('account-import-result').textContent, /リスト 1 件、銘柄 2 件/);
    assert.equal(state.currentListId, 10);
});

test('Claude token creation shows the exact one-time command and supports secure and LAN clipboard use', async () => {
    reset();
    bindAccountSettingsEvents();
    const tick = () => new Promise(resolve => setImmediate(resolve));
    const origin = 'http://192.0.2.18:8000';
    const token = `tql_${'A'.repeat(43)}`;
    const command = `claude mcp add --transport http --scope user tera-quant-lab ${origin}/mcp --header "Authorization: Bearer ${token}"`;
    const prompt = `次のコマンドを実行して、tera-quant-lab の MCP サーバーを設定してください。\n${command}`;
    const writes = [];
    window.location = { origin };
    window.isSecureContext = true;
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: {
        writeText: async value => writes.push(value),
    } });

    const entries = [];
    globalThis.fetch = async (url, options) => {
        assert.equal(url, '/user/account%2F7/api-tokens');
        assert.equal(options.cache, 'no-store');
        if (options.method === 'POST') {
            assert.deepEqual(JSON.parse(options.body), { name: 'Claude Code', expires_in_days: 90 });
            entries.push({ id: 1, name: 'Claude Code', token_prefix: token.slice(0, 12) });
            return { ok: true, status: 201, json: async () => ({ ...entries[0], token }) };
        }
        return { ok: true, status: 200, json: async () => entries };
    };
    byId('account-token-name').value = ' Claude Code ';
    byId('account-token-expiry').value = '90';
    await openAccountSettings();
    assert.equal(byId('account-token-empty').classList.contains('hidden'), false);
    byId('account-token-form').requestSubmit();
    await tick();
    assert.equal(byId('account-token-once').classList.contains('hidden'), false);
    assert.equal(byId('account-token-value').value, token);
    assert.equal(byId('account-token-command').value, command);
    assert.match(byId('account-token-status').textContent, /APIトークンを作成しました/);
    assert.equal(document.activeElement, byId('account-token-command-copy'));
    for (const id of ['account-token-copy', 'account-token-command-copy', 'account-token-prompt-copy']) byId(id).click();
    await tick();
    assert.deepEqual(writes, [token, command, prompt]);
    assert.match(byId('toast-container').children.at(-1).innerHTML, /コピーしました/);

    window.isSecureContext = false;
    for (const [button, id, expected] of [
        ['account-token-copy', 'account-token-value', token],
        ['account-token-command-copy', 'account-token-command', command],
        ['account-token-prompt-copy', 'account-token-prompt', prompt],
    ]) {
        const field = byId(id);
        let selection;
        field.select = () => { selection = field.value; };
        byId(button).click();
        assert.equal(document.activeElement, field);
        assert.equal(selection, expected);
    }
    assert.equal(writes.length, 3);
    assert.equal(byId('account-token-prompt-fallback').classList.contains('hidden'), false);
    assert.match(byId('toast-container').children.at(-1).innerHTML, /選択したテキストをコピー/);
    byId('account-token-dismiss').click();
    assert.equal(byId('account-token-once').classList.contains('hidden'), true);
    for (const id of ['account-token-value', 'account-token-command', 'account-token-prompt']) assert.equal(byId(id).value, '');
    assert.ok(!localStorage.getItem('stockAnalyzerUser').includes(token));
    assert.ok(!JSON.stringify([...cache]).includes(token));
});

test('token list treats names as text and revokes only after confirmation', async () => {
    reset();
    bindAccountSettingsEvents();
    const payload = '"><img src=x onerror=alert(1)>';
    const entries = [{ id: 42, name: payload, token_prefix: 'tql_AbCdEfGh', expired: true,
        created_at: '2026-01-01T00:00:00Z', expires_at: '2026-04-01T00:00:00Z', last_used_at: null }];
    const revoked = [];
    globalThis.fetch = async (url, options) => {
        if (options.method !== 'DELETE') return { ok: true, status: 200, json: async () => entries };
        revoked.push(url);
        entries.length = 0;
        return { ok: true, status: 204 };
    };
    await openAccountSettings();
    const list = byId('account-token-list');
    const [heading, dates, button] = list.children[0].children;
    assert.equal(heading.children[0].textContent, payload);
    assert.equal(heading.children[0].innerHTML, '');
    assert.match(heading.children[1].textContent, /tql_AbCdEfGh…/);
    assert.equal(heading.children[2].textContent, '期限切れ');
    assert.deepEqual(dates.children.filter((_, index) => index % 2 === 0).map(child => child.textContent),
        ['作成日', '最終使用', '有効期限']);
    assert.match(button.getAttribute('aria-label'), /無効化/);
    button.click();
    assert.ok(byId('revoke-token-message').textContent.includes(payload));
    assert.equal(byId('revoke-token-message').innerHTML, '');
    assert.equal(document.activeElement, byId('revoke-token-message'));
    byId('revoke-token-cancel').click();
    assert.equal(byId('revoke-token-overlay').classList.contains('hidden'), true);
    assert.deepEqual(revoked, []);
    button.click();
    byId('revoke-token-form').requestSubmit();
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(revoked, ['/user/account%2F7/api-tokens/42']);
    assert.equal(list.children.length, 0);
    assert.equal(byId('account-token-empty').classList.contains('hidden'), false);
});
