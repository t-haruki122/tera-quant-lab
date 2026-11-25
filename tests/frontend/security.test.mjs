import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';
import { elements, storage, fromRepo } from './helpers/dom-shim.mjs';

const { state, cache } = await fromRepo('frontend/js/state.js');
const { fetchAPI } = await fromRepo('frontend/js/api.js');
const { submitAuth, logoutUser } = await fromRepo('frontend/js/auth.js');
const response = (data, status = 200) => ({ ok: status < 400, status, json: async () => data });
let handler;
globalThis.fetch = async (url, options) => url === '/stats' ? response({}, 503) : handler(url, options);

test('frontend HTML and templates use stylesheet classes or CSSOM for styles', () => {
    for (const file of ['frontend/index.html', 'frontend/js/detail.js', 'frontend/js/list.js',
                        'frontend/js/account.js', 'frontend/js/action-modal.js']) {
        assert.doesNotMatch(readFileSync(resolve(file), 'utf8'), /\sstyle\s*=/i, file);
    }
});

test('frontend sources contain no native prompt, confirm or alert calls', () => {
    for (const file of readdirSync('frontend', { recursive: true, withFileTypes: true })) {
        if (!file.isFile() || !/\.(?:html|js|mjs|css)$/i.test(file.name)) continue;
        const path = resolve(file.parentPath, file.name);
        assert.doesNotMatch(readFileSync(path, 'utf8'), /\b(?:prompt|confirm|alert)\s*\(|\[\s*['"](?:prompt|confirm|alert)['"]\s*\]\s*\(/, path);
    }
});

function resetBrowser() {
    elements.clear();
    storage.clear();
    cache.clear();
    state.currentUser = null;
    state.currentListId = null;
    state.listItems = [];
    handler = () => { throw new Error('Unexpected fetch: install a fake handler'); };
}

test('authentication and session isolation', async t => {
    resetBrowser();
    async function check(name, condition) {
        await t.test(name, () => assert.ok(condition, name));
    }
    state.currentUser = { id: 1, username: 'alice', access_token: 'alice-token' };
    let sent;
    handler = (url, options) => { sent = options; return response({ ok: true }); };
    await fetchAPI('/custom', { method: 'POST', headers: { 'X-Custom': 'retained' } });
    await check('fetchAPI preserves Content-Type, custom header and bearer token',
        sent.headers.get('Content-Type') === 'application/json'
        && sent.headers.get('X-Custom') === 'retained'
        && sent.headers.get('Authorization') === 'Bearer alice-token');
    cache.set('old-user', {});
    localStorage.setItem('stockAnalyzerUser', 'old-user');
    logoutUser();
    await check('logout clears stored user, list state and GET cache',
        state.currentUser === null && state.currentListId === null && state.listItems.length === 0
        && !storage.has('stockAnalyzerUser') && cache.size === 0);
    
    state.authMode = 'register';
    document.getElementById('auth-username').value = 'bob';
    document.getElementById('auth-password').value = '1234567';
    let requests = 0;
    handler = () => { requests++; return response({}); };
    await submitAuth();
    await check('frontend rejects 7-character registration with Japanese message',
        requests === 0 && document.getElementById('auth-error').textContent.includes('8文字'));
    
    const loginError = 'ユーザー名またはパスワードが正しくありません';
    for (const mode of ['login', 'register']) {
        state.authMode = mode;
        document.getElementById('auth-modal-overlay').classList.add('hidden');
        document.getElementById('auth-username').value = 'bob';
        document.getElementById('auth-password').value = 'wrong-password';
        cache.set('preserve-on-failed-login', { data: 'sentinel' });
        const toastCount = document.getElementById('toast-container').children.length;
        let loginRequests = 0;
        handler = (url, options) => {
            assert.equal(url, '/user/login');
            assert.equal(options.headers.has('Authorization'), false);
            loginRequests++;
            return response({ detail: loginError }, 401);
        };
        await assert.rejects(fetchAPI('/user/login', { method: 'POST', body: '{}' }),
            error => error.message === loginError);
        await check(`failed login 401 without token in ${mode} mode propagates detail without logout/modal`,
            loginRequests === 1 && state.currentUser === null && state.authMode === mode
            && cache.has('preserve-on-failed-login')
            && document.getElementById('auth-username').value === 'bob'
            && document.getElementById('auth-password').value === 'wrong-password'
            && document.getElementById('auth-modal-overlay').classList.contains('hidden')
            && document.getElementById('toast-container').children.length === toastCount);
    }
    state.authMode = 'login';
    await submitAuth();
    await check('submitAuth displays failed-login detail and preserves entered credentials',
        document.getElementById('auth-error').textContent === loginError
        && !document.getElementById('auth-error').classList.contains('hidden')
        && document.getElementById('auth-username').value === 'bob'
        && document.getElementById('auth-password').value === 'wrong-password'
        && state.authMode === 'login');
    
    state.authMode = 'login';
    document.getElementById('auth-password').value = 'old';
    cache.set('old-user', {});
    handler = url => {
        if (url === '/user/login') return response({ id: 2, username: 'bob', access_token: 'bob-token', token_type: 'bearer' });
        if (url === '/user/2/lists') return response({ lists: [{ id: 20, name: 'マイリスト' }], default_list_id: 20 });
        if (url === '/user/2/lists/20/summary') return response({ items: [] });
        throw new Error(`Unexpected URL: ${url}`);
    };
    await submitAuth();
    await check('login accepts short legacy password, stores token and clears prior cache',
        state.currentUser?.access_token === 'bob-token'
        && JSON.parse(storage.get('stockAnalyzerUser')).access_token === 'bob-token'
        && !cache.has('old-user') && state.currentListId === 20);
    
    handler = () => response({ detail: 'Expired' }, 401);
    await assert.rejects(fetchAPI('/user/2/lists'), /Expired/);
    await check('401 clears session and cache and shows login',
        state.currentUser === null && !storage.has('stockAnalyzerUser') && cache.size === 0
        && state.authMode === 'login'
        && !document.getElementById('auth-modal-overlay').classList.contains('hidden'));
    
    state.currentUser = { id: 1, access_token: 'alice-token' };
    let finish;
    handler = () => new Promise(resolve => { finish = resolve; });
    const pending = fetchAPI('/user/1/lists');
    logoutUser();
    state.currentUser = { id: 2, access_token: 'bob-token' };
    finish(response({ lists: ['alice private list'] }));
    await assert.rejects(pending, /ログイン状態/);
    await check('in-flight response from previous session cannot repopulate cache', cache.size === 0);
    
});

test('escaped markup and safe links', async t => {
    resetBrowser();
    async function check(name, condition) {
        await t.test(name, () => assert.ok(condition, name));
    }
    const { escapeHtml, escapeAttr, safeHttpUrl, normalizeTagHue, showToast } = await fromRepo('frontend/js/utils.js');
    const { renderProfile, renderIndicators, renderStockHeader, loadNews, loadFinancials } = await fromRepo('frontend/js/detail.js');
    const { renderListTable, openFilterModal, openTagModal, loadDefaultList } = await fromRepo('frontend/js/list.js');
    const { updateAccountUI } = await fromRepo('frontend/js/auth.js');
    await check('escapeHtml escapes ampersand, angle brackets, double quote and apostrophe',
        escapeHtml(`&<>"'`) === '&amp;&lt;&gt;&quot;&#39;');
    await check('escapeHtml handles null, undefined, numbers and objects',
        escapeHtml(null) === '' && escapeHtml(undefined) === '' && escapeHtml(123) === '123'
        && escapeHtml({ toString: () => '<object>' }) === '&lt;object&gt;');
    await check('escapeAttr uses the same five-character escaping', escapeAttr(`&<>"'`) === '&amp;&lt;&gt;&quot;&#39;');
    const payload = `"><img src=x onerror=alert(1)>&'`;
    // No jsdom dependency is installed: inspect the actual strings assigned to our
    // DOM shim. Encoded angle brackets/quotes cannot become HTML tags/attributes.
    async function safeMarkup(name, html, expectedText = payload) {
        const tags = html.match(/<[^>]*>/g) || [];
        await check(name, html.includes(escapeHtml(expectedText)) && !/<img\b/i.test(html)
            && tags.every(tag => !/\s(?:onerror|src)\s*=/i.test(tag.replace(/"[^"]*"/g, '""'))) && !html.includes(payload));
    }
    renderStockHeader({ symbol: payload, price: 12, timestamp: '2026-01-01' },
                      { name: payload, sector: payload, currency: 'USD' });
    await check('company name, symbol and sector header use textContent',
        document.getElementById('company-name').textContent === payload
        && document.getElementById('ticker-badge').textContent === payload
        && document.getElementById('stock-meta').textContent === payload);
    renderProfile({ sector: payload, industry: payload, country: payload, currency: payload,
                    summary: payload, website: 'javascript:alert(1)' });
    await safeMarkup('profile fields and summary render as escaped text', document.getElementById('profile-body').innerHTML);
    await check('profile rejects javascript website href', !document.getElementById('profile-body').innerHTML.includes('href='));
    renderProfile({ website: 'https://example.com/?q="&x=\'test' });
    await check('profile HTTP website has escaped href and safe rel',
        document.getElementById('profile-body').innerHTML.includes('rel="noopener noreferrer"')
        && document.getElementById('profile-body').innerHTML.includes('&amp;'));
    renderIndicators({ roe: payload, eps: payload });
    await safeMarkup('indicator API values render as escaped text', document.getElementById('indicators-body').innerHTML);
    state.listSearchQuery = '';
    state.activeTagFilter = payload;
    state.listItems = [{ symbol: payload, name: payload, memo: payload, tags: [payload], loading: false,
                         price: payload, currency: 'invalid-currency' }];
    renderListTable();
    await safeMarkup('company, symbol, memo title, price fallback and tags cannot break table attributes',
               document.getElementById('comparison-tbody').innerHTML);
    await check('memo title remains one quoted escaped attribute',
        document.getElementById('comparison-tbody').innerHTML.includes(`title="${escapeHtml(payload)}"`));
    await safeMarkup('active tag filter escapes text', document.getElementById('active-filter-tag').innerHTML);
    openFilterModal();
    await safeMarkup('tag filter data-tag escapes both quote types', document.getElementById('filter-tags-container').innerHTML);
    await check('filter tag stays in a quoted data attribute',
        document.getElementById('filter-tags-container').innerHTML.includes(`data-tag="${escapeHtml(payload)}"`));
    openTagModal(payload);
    await safeMarkup('tag editor data-tag escapes both quote types', document.getElementById('tag-current-tags').innerHTML);
    state.listItems = [{ symbol: payload, loading: true }];
    state.activeTagFilter = null;
    renderListTable();
    await safeMarkup('loading row symbol and button data-symbol are escaped', document.getElementById('comparison-tbody').innerHTML);
    await check('malicious hue falls back to a finite number and numeric values clamp to 0..360',
        normalizeTagHue(payload) === 210 && normalizeTagHue('999') === 360
        && normalizeTagHue(-5) === 0 && normalizeTagHue('120.5') === 120.5
        && normalizeTagHue(Infinity) === 210);
    const hueMarkup = ['active-filter-tag', 'filter-tags-container', 'tag-current-tags']
        .map(id => document.getElementById(id).innerHTML).join('');
    const hues = [...hueMarkup.matchAll(/data-tag-hue="([^"]+)"/g)].map(match => match[1]);
    await check('all rendered tag style values are finite numbers in 0..360', hues.length >= 3
        && hues.every(hue => /^\d+(?:\.\d+)?$/.test(hue) && Number(hue) >= 0 && Number(hue) <= 360));
    await check('safeHttpUrl rejects non-HTTP and malformed URLs',
        ['javascript:alert(1)', 'data:text/html,<img>', '/relative', 'not a url', null]
            .every(url => safeHttpUrl(url) === null));
    cache.clear();
    handler = () => response({ news: [
        { title: payload, url: 'javascript:alert(1)', published_at: payload, source: payload, publisher: payload },
        { title: payload, url: 'https://example.com/?q="&x=\'test', published_at: '2026-01-01' },
    ] });
    await loadNews('XSS');
    const news = document.getElementById('news-body').innerHTML;
    await safeMarkup('news title and invalid publication date render as escaped text', news);
    await check('javascript news URL is plain text; HTTPS link has escaped href, target and rel',
        !news.includes('javascript:') && (news.match(/<a\b/g) || []).length === 1
        && news.includes('href="https://example.com/') && news.includes('&amp;')
        && news.includes('target="_blank" rel="noopener noreferrer"')
        && news.includes('<div class="news-item">'));
    cache.clear();
    handler = () => response({ detail: payload }, 503);
    await loadNews('ERROR');
    await safeMarkup('news error detail renders as escaped text', document.getElementById('news-body').innerHTML);
    await loadFinancials('ERROR', {});
    await safeMarkup('financial error detail renders as escaped text', document.getElementById('financials-body').innerHTML);
    showToast(payload, 'error');
    await safeMarkup('toast API error/message renders as escaped text', document.getElementById('toast-container').children.at(-1).innerHTML);
    state.currentUser = { id: 9, username: payload, access_token: 'keep-token' };
    updateAccountUI();
    await check('username uses textContent', document.getElementById('account-btn-label').textContent === payload
        && document.getElementById('account-menu-username').textContent === `👤 ${payload}`);
    cache.clear();
    handler = url => response(url.endsWith('/lists')
        ? { lists: [{ id: 90, name: payload }], default_list_id: 90 } : { items: [] });
    await loadDefaultList();
    await safeMarkup('watchlist selector escapes an API list name', document.getElementById('watchlist-select').innerHTML);
    await check('raw list name cannot appear in markup',
        state.currentListId === 90 && [...elements.values()].every(el => !el.innerHTML.includes(payload)));
    // Audit all other JS modules too: their API values are textContent, input value,
    // Chart.js canvas data or fetch parameters; there are no additional HTML sinks.
    for (const file of ['frontend/app.js', 'frontend/js/auth.js', 'frontend/js/account.js', 'frontend/js/api.js', 'frontend/js/chart.js',
                        'frontend/js/financial-chart.js', 'frontend/js/stats.js', 'frontend/js/state.js']) {
        await check(`${file} has no HTML string sinks or inline event attributes`,
            !/innerHTML|insertAdjacentHTML|outerHTML|onclick\s*=/.test(readFileSync(resolve(file), 'utf8')));
    }
});

test('rate-limit errors preserve the auth form', async t => {
    resetBrowser();
    async function check(name, condition) {
        await t.test(name, () => assert.ok(condition, name));
    }
    for (const mode of ['login', 'register']) {
        state.authMode = mode;
        document.getElementById('auth-modal-overlay').classList.remove('hidden');
        document.getElementById('auth-username').value = 'bob';
        document.getElementById('auth-password').value = 'password123';
        const userBefore = state.currentUser;
        cache.set('preserve-on-429', { data: 'sentinel' });
        const toastCount = document.getElementById('toast-container').children.length;
        const detail = '試行回数が多すぎます。しばらくしてから再試行してください。';
        handler = url => {
            assert.equal(url, `/user/${mode}`);
            return response({ detail }, 429);
        };
        await submitAuth();
        await check(`429 on ${mode} displays Japanese detail in auth modal without logout`,
            document.getElementById('auth-error').textContent === detail
            && !document.getElementById('auth-error').classList.contains('hidden')
            && !document.getElementById('auth-modal-overlay').classList.contains('hidden')
            && state.currentUser === userBefore && state.authMode === mode && cache.has('preserve-on-429')
            && document.getElementById('auth-password').value === 'password123'
            && document.getElementById('toast-container').children.length === toastCount);
    }
});
