import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import test from 'node:test';
import { elements, fromRepo } from './helpers/dom-shim.mjs';

const { state, cache } = await fromRepo('frontend/js/state.js');
const { loadNews, loadFinancials } = await fromRepo('frontend/js/detail.js');
const { loadChart } = await fromRepo('frontend/js/chart.js');
const { editStockMemo } = await fromRepo('frontend/js/list.js');

test('every dynamic API path segment is encoded in the actual frontend templates', () => {
    const files = ['frontend/app.js', ...readdirSync('frontend/js').filter(f => f.endsWith('.js')).map(f => `frontend/js/${f}`)];
    const paths = new Set();
    for (const file of files) {
        const source = readFileSync(file, 'utf8');
        for (const match of source.matchAll(/`(\/(?:stock|user|forex)\/[^`]+)`/g)) {
            const path = match[1].split('?')[0];
            const expressions = [...path.matchAll(/\$\{([^}]+)\}/g)];
            for (const [, expression] of expressions) {
                assert.match(expression, /^encodeURIComponent\([^)]+\)$/, `${file}: ${path}`);
            }
            for (const symbol of ['^N225', 'BRK.B', '7203.T', 'A/B?# %']) {
                const userState = { currentUser: { id: 'user/id' }, currentListId: 'list/id' };
                const actual = Function('symbol', 'state', `return \`${path}\`;`)(symbol, userState);
                const expected = path.replaceAll('${encodeURIComponent(symbol)}', encodeURIComponent(symbol))
                    .replaceAll('${encodeURIComponent(state.currentUser.id)}', 'user%2Fid')
                    .replaceAll('${encodeURIComponent(state.currentListId)}', 'list%2Fid');
                assert.equal(actual, expected);
                paths.add(actual);
            }
        }
    }
    const symbol = encodeURIComponent('^N225');
    const user = '/user/user%2Fid';
    const items = `${user}/lists/list%2Fid/items`;
    for (const path of [
        `/stock/${symbol}`, `/stock/${symbol}/history`, `/stock/${symbol}/profile`, `/stock/${symbol}/indicators`,
        `${user}/default-list`, `${user}/memo/${symbol}`, items, `${items}/${symbol}/tags`,
    ]) {
        assert.ok(paths.has(path), `audit must cover ${path}`);
    }
});

test('data loaders and memo writes send encoded paths through fetchAPI', async () => {
    const sent = [];
    globalThis.fetch = async (url, options) => {
        if (url !== '/stats') sent.push([url, options.method || 'GET']);
        return { ok: true, status: 200, json: async () => ({ news: [], history: [], memo: 'saved' }) };
    };
    globalThis.prompt = () => 'saved';
    state.currentUser = { id: 7, access_token: 'test' };
    state.currentListId = 8;
    state.listItems = [];
    state.currentSymbol = '';
    for (const id of ['price-chart', 'rsi-chart']) {
        const canvas = document.getElementById(id);
        canvas.getContext = () => ({ clearRect() {} });
        elements.set(id, canvas);
    }
    for (const symbol of ['^N225', 'BRK.B', '7203.T', 'A/B?# %']) {
        cache.clear();
        sent.length = 0;
        await loadNews(symbol);
        await loadFinancials(symbol, {});
        await loadChart(symbol, '1mo', 'USD');
        await editStockMemo(symbol);
        const encoded = encodeURIComponent(symbol);
        assert.deepEqual(sent.map(([url]) => url.split('?')[0]), [
            `/stock/${encoded}/news`, `/stock/${encoded}/financials`,
            `/stock/${encoded}/financials/history`, `/stock/${encoded}/dividends/history`,
            `/stock/${encoded}/history`, `/user/7/memo/${encoded}`,
        ]);
        assert.deepEqual(sent.at(-1), [`/user/7/memo/${encoded}`, 'PUT']);
    }
});
