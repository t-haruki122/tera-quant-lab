/**
 * Stock Analyzer — API helper and response cache
 */

import { API_BASE, CACHE_TTL, cache, state } from './state.js';
import { logoutUser, openAuthModal } from './auth.js';

export async function fetchAPI(endpoint, options = {}, errorMessage = 'リクエストに失敗しました') {
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
        const { detail } = (await res.json().catch(() => null)) ?? {};
        const message = Array.isArray(detail)
            ? detail.map(item => item?.msg).filter(msg => typeof msg === 'string' && msg.trim()).join(', ')
            : typeof detail === 'string' && detail.trim();
        const requestId = res.status >= 500 ? res.headers?.get('X-Request-ID') : null;
        const requestIdSuffix = requestId ? `（リクエストID: ${requestId}）` : '';
        throw Object.assign(new Error((message || `${errorMessage}（HTTP ${res.status}）`) + requestIdSuffix), {
            status: res.status,
        });
    }

    const data = await res.json();

    // Ignore responses from a session that ended while the request was in flight.
    if (state.currentUser !== requestUser) {
        throw new Error('ログイン状態が変更されました');
    }

    if (!options.method || options.method === 'GET') {
        cache.set(cacheKey, { data, timestamp: Date.now() });
    }

    return data;
}
