/**
 * Stock Analyzer — API helper and response cache
 */

import { API_BASE, CACHE_TTL, cache, state } from './state.js';
import { logoutUser, openAuthModal } from './auth.js';

export async function fetchAPI(endpoint, options = {}, errorMessage = 'リクエストに失敗しました') {
    const { anonymous = false, responseType, ...requestOptions } = options;
    const requestUser = anonymous ? null : state.currentUser;
    const cacheKey = (requestUser?.access_token || '') + endpoint + JSON.stringify(options);
    const useCache = (!options.method || options.method === 'GET') && options.cache !== 'no-store' && !responseType;

    if (useCache) {
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
        ...requestOptions,
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
            retryAfter: res.headers?.get('Retry-After'),
        });
    }

    const data = res.status === 204 ? null : await (responseType === 'blob' ? res.blob() : res.json());

    // Ignore responses from a session that ended while the request was in flight.
    if (!anonymous && state.currentUser !== requestUser) {
        throw new Error('ログイン状態が変更されました');
    }

    if (useCache && !data?.stale) {
        cache.set(cacheKey, { data, timestamp: Date.now() });
    }

    return data;
}

export async function downloadAPI(endpoint, filename) {
    const blob = await fetchAPI(endpoint, { responseType: 'blob', cache: 'no-store' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}
