/**
 * Stock Analyzer — API helper and response cache
 */

import { API_BASE, CACHE_TTL, cache, state } from './state.js';
import { fetchStats } from './stats.js';
import { logoutUser, openAuthModal } from './auth.js';

export async function fetchAPI(endpoint, options = {}) {
    const requestUser = state.currentUser;
    const cacheKey = (requestUser?.access_token || '') + endpoint + JSON.stringify(options);

    if (!options.method || options.method === 'GET') {
        const cached = cache.get(cacheKey);
        if (cached && Date.now() - cached.timestamp < CACHE_TTL) {
            return cached.data;
        }
    }

    const headers = new Headers({ 'Content-Type': 'application/json' });
    new Headers(options.headers).forEach((value, key) => headers.set(key, value));
    if (requestUser?.access_token) {
        headers.set('Authorization', `Bearer ${requestUser.access_token}`);
    }
    const res = await fetch(`${API_BASE}${endpoint}`, {
        ...options,
        headers,
    });

    if (!res.ok) {
        if (res.status === 401 && requestUser?.access_token && state.currentUser === requestUser) {
            logoutUser();
            openAuthModal('login');
        }
        const err = await res.json().catch(() => ({ detail: 'Unknown error' }));
        const detail = Array.isArray(err.detail)
            ? err.detail.map(item => item.msg).join(', ')
            : err.detail;
        throw new Error(detail || `HTTP ${res.status}`);
    }

    const data = await res.json();

    // Ignore responses from a session that ended while the request was in flight.
    if (state.currentUser !== requestUser) {
        throw new Error('ログイン状態が変更されました');
    }

    if (!options.method || options.method === 'GET') {
        cache.set(cacheKey, { data, timestamp: Date.now() });
    }

    fetchStats();
    return data;
}
