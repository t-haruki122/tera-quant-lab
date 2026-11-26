import { fetchAPI } from './api.js';
import { state } from './state.js';

const months = { '1mo': 1, '3mo': 3, '6mo': 6, '1y': 12, '2y': 24, '10y': 120 };
const pending = new Map();

export function fetchHistory(symbol, period, interval = '1d') {
    const today = new Date();
    const year = today.getUTCFullYear();
    const month = today.getUTCMonth() - (months[period] ?? months['3mo']);
    const lastDay = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
    const start = new Date(Date.UTC(year, month, Math.min(today.getUTCDate(), lastDay)));
    const params = new URLSearchParams({
        start_date: start.toISOString().slice(0, 10),
        end_date: today.toISOString().slice(0, 10),
        interval,
    });
    const endpoint = `/stock/${encodeURIComponent(symbol)}/history?${params}`;
    const key = JSON.stringify([state.currentUser?.access_token, endpoint]);
    if (!pending.has(key)) {
        pending.set(key, fetchAPI(endpoint).finally(() => pending.delete(key)));
    }
    return pending.get(key);
}
