/** Price-alert polling and notification delivery. */

import { fetchAPI } from './api.js';
import { formatPercent, showToast } from './utils.js';

export const alertKinds = {
    above: '指定価格以上', below: '指定価格以下', change_pct: '当日の騰落率（絶対値）',
};

export function notificationStatus(api = globalThis.Notification, secure = globalThis.isSecureContext) {
    if (secure === false || typeof api !== 'function' || typeof api.requestPermission !== 'function') {
        return { available: false, message: 'この環境ではブラウザ通知を利用できません。アプリ内でお知らせします。' };
    }
    if (api.permission === 'denied') {
        return { available: false, message: 'ブラウザ通知が拒否されています。アプリ内でお知らせします。' };
    }
    if (api.permission === 'granted') {
        return { available: false, message: 'ブラウザ通知は有効です。' };
    }
    return { available: true, message: 'アプリ内でお知らせします。ブラウザ通知も有効にできます。' };
}

export function bindNotificationButton(button, status, {
    getAPI = () => globalThis.Notification, isSecure = () => globalThis.isSecureContext,
} = {}) {
    const refresh = () => {
        const { available, message } = notificationStatus(getAPI(), isSecure());
        button.disabled = !available;
        status.textContent = message;
    };
    button.addEventListener('click', async () => {
        if (!notificationStatus(getAPI(), isSecure()).available) return;
        button.disabled = true;
        try {
            await getAPI().requestPermission();
            refresh();
        } catch {
            refresh();
            status.textContent = 'ブラウザ通知を有効にできませんでした。アプリ内でお知らせします。';
        }
    });
    return refresh;
}

export function announceAlert(alert, { toast = showToast, NotificationAPI = globalThis.Notification } = {}) {
    const observed = alert.kind === 'change_pct'
        ? formatPercent(alert.observed_value)
        : alert.observed_value.toLocaleString('ja-JP');
    const message = `価格アラート　${alert.symbol}：${alertKinds[alert.kind]}（判定値 ${observed}）`;
    toast(message, 'info');
    if (NotificationAPI?.permission === 'granted') {
        try {
            new NotificationAPI('価格アラート', { body: message });
        } catch {
            // Some browsers support permission but require a service worker to deliver notifications.
        }
    }
}

export function createAlertPolling({
    getUser, isHidden, onResult = () => {}, notify = announceAlert, request = fetchAPI,
    setTimer = setTimeout, clearTimer = clearTimeout, now = Date.now, initialDelay = 3_000, interval = 300_000,
}) {
    let session = null;
    let timer = null;

    function sync() {
        const user = getUser();
        if (session?.user !== user) {
            cancelTimer();
            session = user ? { user, nextAt: now() + initialDelay, failures: 0, pending: false } : null;
        }
        if (isHidden()) cancelTimer();
        else if (session && !session.pending && timer === null) {
            timer = setTimer(run, Math.max(0, session.nextAt - now()));
        }
    }

    function cancelTimer() {
        if (timer !== null) clearTimer(timer);
        timer = null;
    }

    async function run() {
        timer = null;
        const current = session;
        if (!current || current.user !== getUser() || isHidden()) return;
        const { user } = current;
        current.pending = true;
        let delay = interval;
        try {
            const result = await request(`/user/${encodeURIComponent(user.id)}/alerts/check`, { method: 'POST' });
            if (session !== current) return;
            current.failures = 0;
            for (const alert of result.fired) notify(alert);
            onResult(result);
        } catch (error) {
            if (error.status === 429 || error.status >= 500 || !error.status) {
                current.failures += 1;
                const retry = error.retryAfter;
                const retryDelay = /^\d+$/.test(retry) ? Number(retry) * 1000 : Date.parse(retry) - now();
                delay = Math.max(
                    delay, Math.min(3_600_000, interval * 2 ** current.failures),
                    Number.isFinite(retryDelay) ? retryDelay : 0,
                );
            } else current.failures = 0;
        } finally {
            current.pending = false;
            current.nextAt = now() + delay;
            if (session === current) sync();
        }
    }

    return { sync };
}
