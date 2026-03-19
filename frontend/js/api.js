/**
 * Stock Analyzer — API helper and response cache
 */

import { API_BASE, CACHE_TTL, cache } from './state.js';
import { fetchStats } from './stats.js';

export async function fetchAPI(endpoint, options = {}) {
    const cacheKey = endpoint + JSON.stringify(options);

    if (!options.method || options.method === 'GET') {
        const cached = cache.get(cacheKey);
        if (cached && Date.now() - cached.timestamp < CACHE_TTL) {
            return cached.data;
        }
    }

    const res = await fetch(`${API_BASE}${endpoint}`, {
        headers: { 'Content-Type': 'application/json', ...options.headers },
        ...options,
    });

    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Unknown error' }));
        throw new Error(err.detail || `HTTP ${res.status}`);
    }

    const data = await res.json();

    if (!options.method || options.method === 'GET') {
        cache.set(cacheKey, { data, timestamp: Date.now() });
    }

    fetchStats();
    return data;
}
