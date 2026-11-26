import assert from 'node:assert/strict';
import test from 'node:test';
import { fromRepo } from './helpers/dom-shim.mjs';

const { announceAlert, bindNotificationButton, createAlertPolling } = await fromRepo('frontend/js/alerts.js');
const alert = { id: 1, symbol: 'TEST <img src=x onerror=alert(1)>', kind: 'above', observed_value: 105 };
const byId = id => document.getElementById(id);
const tick = () => new Promise(resolve => setImmediate(resolve));

test('polling pauses while hidden, backs off across visibility changes, and stops on logout', async () => {
    let now = 0;
    let hidden = false;
    let user = { id: 'a/b' };
    let planned;
    let release;
    const calls = [];
    const fired = [];
    const poll = createAlertPolling({
        getUser: () => user, isHidden: () => hidden, now: () => now, interval: 300_000,
        setTimer(fn, delay) { assert.equal(planned, undefined); planned = { fn, delay }; return 1; },
        clearTimer() { planned = undefined; },
        notify: hit => fired.push(hit),
        request: async (...args) => {
            calls.push(args);
            if (calls.length === 1) throw Object.assign(new Error('rate limited'), { status: 429, retryAfter: '900' });
            if (calls.length === 2) throw Object.assign(new Error('server error'), { status: 503 });
            if (calls.length === 3) return { fired: [alert], skipped: [] };
            return new Promise(resolve => { release = resolve; });
        },
    });
    const run = () => {
        const { fn, delay } = planned;
        planned = undefined;
        now += delay;
        return fn();
    };
    poll.sync();
    assert.equal(planned.delay, 3_000);
    hidden = true;
    poll.sync();
    assert.equal(planned, undefined);
    now += 3_100;
    hidden = false;
    poll.sync();
    assert.equal(planned.delay, 0);
    await run();
    assert.deepEqual(calls[0], ['/user/a%2Fb/alerts/check', { method: 'POST' }]);
    assert.equal(planned.delay, 900_000);
    hidden = true;
    poll.sync();
    now += 500_000;
    hidden = false;
    poll.sync();
    assert.equal(planned.delay, 400_000);
    await run();
    assert.equal(planned.delay, 1_200_000);
    await run();
    assert.deepEqual(fired, [alert]);
    assert.equal(planned.delay, 300_000);

    const pending = run();
    user = null;
    poll.sync();
    release({ fired: [alert], skipped: [] });
    await pending;
    assert.deepEqual(fired, [alert]);
    assert.equal(planned, undefined);
    assert.equal(calls.length, 4);
    user = { id: 'next' };
    poll.sync();
    assert.equal(planned.delay, 3_000);
});

test('browser permission is requested only on click and granted alerts also produce escaped status toasts', async () => {
    const notices = [];
    let requests = 0;
    class NotificationMock {
        static permission = 'default';
        static async requestPermission() {
            requests += 1;
            this.permission = 'granted';
            return this.permission;
        }
        constructor(title, options) { notices.push({ title, ...options }); }
    }
    const button = byId('alert-notification-enable');
    const status = byId('alert-notification-status');
    const refresh = bindNotificationButton(button, status, { getAPI: () => NotificationMock, isSecure: () => true });
    refresh();
    assert.equal(button.disabled, false);
    announceAlert(alert, { NotificationAPI: NotificationMock });
    assert.equal(requests, 0);
    assert.deepEqual(notices, []);
    button.click();
    await tick();
    assert.equal(requests, 1);
    assert.match(status.textContent, /通知は有効/);
    button.click();
    announceAlert(alert, { NotificationAPI: NotificationMock });
    assert.equal(requests, 1);
    assert.equal(notices.length, 1);
    assert.match(notices[0].body, /判定値 105/);
    for (const toast of byId('toast-container').children) {
        assert.equal(toast.getAttribute('role'), 'status');
        assert.match(toast.innerHTML, /&lt;img/);
        assert.doesNotMatch(toast.innerHTML, /<img/);
    }
});

test('denied, unsupported and insecure contexts explain the toast fallback', async () => {
    const button = byId('permission-denied-button');
    const status = byId('permission-denied-status');
    let requests = 0;
    class PermissionDenied {
        static permission = 'default';
        static async requestPermission() { requests += 1; this.permission = 'denied'; }
        constructor() { assert.fail('Should not send a denied browser Notification'); }
    }
    const refresh = bindNotificationButton(button, status, { getAPI: () => PermissionDenied, isSecure: () => true });
    refresh();
    button.click();
    await tick();
    assert.equal(requests, 1);
    assert.equal(button.disabled, true);
    assert.match(status.textContent, /拒否.*アプリ内/);
    announceAlert(alert, { NotificationAPI: PermissionDenied });
    for (const [api, secure] of [[undefined, true], [PermissionDenied, false]]) {
        const unsupportedButton = document.createElement('button');
        const output = document.createElement('p');
        bindNotificationButton(unsupportedButton, output, { getAPI: () => api, isSecure: () => secure })();
        unsupportedButton.click();
        assert.equal(unsupportedButton.disabled, true);
        assert.match(output.textContent, /利用できません.*アプリ内/);
    }
    assert.equal(requests, 1);
});
