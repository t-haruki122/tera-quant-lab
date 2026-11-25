import assert from 'node:assert/strict';
import test from 'node:test';
import { elements, fromRepo, pressKey } from './helpers/dom-shim.mjs';

process.env.TZ = 'Asia/Tokyo';
const response = (body, status = 200) => ({ ok: status === 200, status, json: async () => body });
globalThis.fetch = async () => response({ rate: 150 });
const { App } = await fromRepo('frontend/app.js');
const { fetchAPI } = await fromRepo('frontend/js/api.js');
const { loadFinancials, loadNews, renderStockHeader, showSection } = await fromRepo('frontend/js/detail.js');
const { renderListTable } = await fromRepo('frontend/js/list.js');
const { cache, state } = await fromRepo('frontend/js/state.js');
const { formatNewsDate } = await fromRepo('frontend/js/utils.js');
const byId = id => document.getElementById(id);
const cached = { stale: true, cached_at: '2026-11-25T10:00:00Z' };
const payload = '<img src=x onerror="alert(1)">';
await new Promise(resolve => setImmediate(resolve));

test('older Enter handlers ignore IME composition and keyCode 229', () => {
    elements.clear();
    const calls = [];
    const handlers = {
        'search-input': 'searchStock',
        'list-add-modal-input': 'submitListAddFromModal',
        'tag-input': 'addTagFromInput',
        'auth-password': 'submitAuth',
    };
    const deps = Object.fromEntries(Object.values(handlers).map(name => [name, () => calls.push(name)]));
    new App(deps).bindCoreEvents();
    for (const id of Object.keys(handlers)) {
        const input = byId(id);
        pressKey(input, input, 'Enter', { isComposing: true, keyCode: 13 });
        pressKey(input, input, 'Enter', { isComposing: false, keyCode: 229 });
        pressKey(input, input, 'Escape');
    }
    assert.deepEqual(calls, []);
    for (const id of Object.keys(handlers)) {
        const input = byId(id);
        pressKey(input, input, 'Enter', { isComposing: false, keyCode: 13 });
    }
    assert.deepEqual(calls, Object.values(handlers));
});

test('news shows the local date, time and escaped publisher', async () => {
    cache.clear();
    assert.equal(formatNewsDate(cached.cached_at), '2026/11/25 19:00');
    assert.equal(formatNewsDate('garbage'), '');
    assert.equal(formatNewsDate(null), '');
    globalThis.fetch = async () => response({
        ...cached,
        news: [
            { title: 'Article', published_at: cached.cached_at, publisher: payload, url: 'https://example.test' },
            { title: 'Undated', published_at: null, source: 'Publisher', url: 'https://example.test/undated' },
        ],
    });
    await loadNews('TEST');
    const html = byId('news-body').innerHTML;
    assert.match(html, /最新データを取得できなかったため、2026\/11\/25 19:00 時点/);
    assert.match(html, /2026\/11\/25 19:00 · &lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
    assert.match(html, /class="news-date">Publisher<\/span>/);
    assert.doesNotMatch(html, /<img/);
});

test('stale notices render as text on detail and escape markup on watchlist rows', () => {
    state.exchangeRateCachedAt = null;
    const unsafe = { stale: true, cached_at: payload };
    const quote = { symbol: 'TEST', price: 10, timestamp: cached.cached_at };
    renderStockHeader({ ...quote, ...unsafe }, { name: 'Test Company' });
    assert.equal(byId('quote-stale').textContent,
        `最新データを取得できなかったため、${payload} 時点のデータを表示しています`);
    assert.equal(byId('quote-stale').innerHTML, '');
    renderStockHeader(quote, { name: 'Test Company' });
    assert.equal(byId('quote-stale').textContent, '');

    state.listItems = [{ ...quote, ...unsafe }, { symbol: 'FRESH', price: 30 }];
    renderListTable();
    const html = byId('comparison-tbody').innerHTML;
    assert.equal((html.match(/class="stale-notice/g) || []).length, 1);
    assert.match(html, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
    assert.doesNotMatch(html, /<img/);
});

test('forex notice updates on the detail and watchlist even when the numerical rate stays the same', async () => {
    state.exchangeRateUSDJPY = 150;
    state.exchangeRateCachedAt = null;
    state.listItems = [
        { symbol: 'USD', currency: 'USD', price: 100 },
        { symbol: 'JPY', currency: 'JPY', price: 100 },
    ];
    const quote = { symbol: 'USD', price: 100, timestamp: cached.cached_at };
    let fx = { rate: 150, ...cached };
    const app = new App({ fetchAPI: async () => fx });
    app.currentView = 'detail';
    renderStockHeader(quote, { currency: 'USD' });
    showSection('stock-detail');
    await app.fetchExchangeRate();
    assert.match(byId('quote-stale').textContent, /2026\/11\/25 19:00 時点/);
    assert.equal((byId('comparison-tbody').innerHTML.match(/class="stale-notice/g) || []).length, 1);
    fx = { rate: 150 };
    await app.fetchExchangeRate();
    assert.equal(byId('quote-stale').textContent, '');
    assert.doesNotMatch(byId('comparison-tbody').innerHTML, /class="stale-notice/);
});

test('financials still show stale values when the uncached histories fail', async () => {
    cache.clear();
    globalThis.fetch = async path => path.endsWith('/financials')
        ? response({ eps: 25, ...cached })
        : response({ detail: payload }, 503);
    await loadFinancials('TEST', {});
    const html = byId('financials-body').innerHTML;
    assert.match(html, /最新データを取得できなかったため、2026\/11\/25 19:00 時点/);
    assert.match(html, /25\.00/);
    assert.match(html, /&lt;img/);
    assert.doesNotMatch(html, /<img/);
});

test('browser API cache retries a stale result on the next lookup', async () => {
    cache.clear();
    let calls = 0;
    globalThis.fetch = async () => response(++calls === 1 ? { price: 1, ...cached } : { price: 2 });
    assert.equal((await fetchAPI('/stock/TEST')).stale, true);
    assert.deepEqual(await fetchAPI('/stock/TEST'), { price: 2 });
    assert.deepEqual(await fetchAPI('/stock/TEST'), { price: 2 });
    assert.equal(calls, 2);
});
