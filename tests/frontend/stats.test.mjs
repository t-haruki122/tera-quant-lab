import assert from 'node:assert/strict';
import test from 'node:test';
import { fromRepo } from './helpers/dom-shim.mjs';

const requests = [];
globalThis.fetch = async url => {
    requests.push(url);
    return { ok: true, json: async () => ({ rate: 150 }) };
};
const { App } = await fromRepo('frontend/app.js');
const { updateStatsDashboard } = await fromRepo('frontend/js/stats.js');

test('polls only in the visible stats view and pauses while the document is hidden', async () => {
    const timers = new Map();
    let id = 0;
    let fetches = 0;
    const viewEvents = new EventTarget();
    const viewDocument = {
        hidden: false, addEventListener: viewEvents.addEventListener.bind(viewEvents),
        getElementById: id => document.getElementById(id), querySelectorAll: () => [],
    };
    const app = new App({
        documentRef: viewDocument,
        fetchAPI: async () => ({ rate: 150 }),
        fetchStats: () => fetches++,
        setIntervalFn: (callback, ms) => {
            assert.equal(ms, 10_000);
            timers.set(++id, callback);
            return id;
        },
        clearIntervalFn: key => timers.delete(key),
    });
    await app.init();
    assert.equal(fetches, 0);
    assert.ok(!requests.includes('/stats'));
    app.switchView('stats');
    assert.equal(fetches, 1);
    assert.equal(timers.size, 1);
    const [tick] = timers.values();
    tick();
    assert.equal(fetches, 2);
    viewDocument.hidden = true;
    viewEvents.dispatchEvent(new Event('visibilitychange'));
    assert.equal(timers.size, 0);
    tick();
    assert.equal(fetches, 2);
    viewDocument.hidden = false;
    viewEvents.dispatchEvent(new Event('visibilitychange'));
    assert.equal(fetches, 3);
    assert.equal(timers.size, 1);
    app.switchView('detail');
    assert.equal(timers.size, 0);
    viewEvents.dispatchEvent(new Event('visibilitychange'));
    assert.equal(fetches, 3);
    viewDocument.hidden = true;
    app.switchView('stats');
    assert.equal(fetches, 3);
    assert.equal(timers.size, 0);
});

test('daily stats and the cache hit rate render as text', () => {
    globalThis.Chart = class {};
    const rate = document.getElementById('dash-cache-hit-rate');
    updateStatsDashboard({ cache_hit_rate: 0.125, last_7_days: [{ date: '2026-11-26', hits: 4, errors: 1, total_ms: 10 }] });
    assert.equal(rate.textContent, '12.5%');
    const day = document.getElementById('dash-daily-totals').children[0];
    assert.deepEqual(day.children.map(cell => cell.textContent), ['2026-11-26', 4, 1, '2.5 ms']);
});
