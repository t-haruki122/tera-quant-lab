/**
 * Stock Analyzer — Shared UI and formatting helpers
 */

import { state } from './state.js';

const percentFormatter = new Intl.NumberFormat('ja-JP', { maximumFractionDigits: 2, minimumFractionDigits: 2, signDisplay: 'exceptZero' });

export function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.setAttribute('role', type === 'error' ? 'alert' : 'status');
    if (type !== 'error') toast.setAttribute('aria-live', 'polite');
    container.appendChild(toast);

    const icons = { success: '✓', error: '✕', warning: '⚠', info: 'ℹ' };
    toast.innerHTML = `<span class="toast-icon" aria-hidden="true">${escapeHtml(icons[type] || 'ℹ')}</span><span class="toast-message">${escapeHtml(message)}</span>`;

    setTimeout(() => {
        toast.classList.add('toast-hide');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

export function formatPercent(value) {
    return percentFormatter.format(value) + '%';
}

export function formatCurrency(value, currency) {
    if (value == null) return '—';
    try {
        let displayValue = value;
        let displayCurrency = currency || 'USD';

        // USDの場合は円換算する
        if (displayCurrency === 'USD' && state.exchangeRateUSDJPY) {
            displayValue = value * state.exchangeRateUSDJPY;
            displayCurrency = 'JPY';
        }

        const locale = displayCurrency === 'JPY' ? 'ja-JP' : 'en-US';
        return new Intl.NumberFormat(locale, {
            style: 'currency',
            currency: displayCurrency,
            minimumFractionDigits: displayCurrency === 'JPY' ? 0 : 2,
            maximumFractionDigits: displayCurrency === 'JPY' ? 0 : 2,
        }).format(displayValue);
    } catch {
        return value.toLocaleString();
    }
}

export function formatLargeNumber(value, currency) {
    if (value == null) return '—';

    let displayValue = value;
    let isJPY = currency === 'JPY';

    // USDの場合は円換算
    if ((!currency || currency === 'USD') && state.exchangeRateUSDJPY) {
        displayValue = value * state.exchangeRateUSDJPY;
        isJPY = true;
    }

    const abs = Math.abs(displayValue);

    if (isJPY) {
        if (abs >= 1e12) return '¥' + (displayValue / 1e12).toFixed(2) + '兆';
        if (abs >= 1e8) return '¥' + (displayValue / 1e8).toFixed(2) + '億';
        if (abs >= 1e4) return '¥' + (displayValue / 1e4).toFixed(2) + '万';
        return '¥' + displayValue.toLocaleString();
    } else {
        if (abs >= 1e12) return '$' + (displayValue / 1e12).toFixed(2) + 'T';
        if (abs >= 1e9) return '$' + (displayValue / 1e9).toFixed(2) + 'B';
        if (abs >= 1e6) return '$' + (displayValue / 1e6).toFixed(2) + 'M';
        if (abs >= 1e3) return '$' + (displayValue / 1e3).toFixed(1) + 'K';
        return '$' + displayValue.toLocaleString();
    }
}

export function formatAbbreviatedNumber(value) {
    const abs = Math.abs(Number(value));
    if (abs >= 1_000_000_000_000) return `${(value / 1_000_000_000_000).toFixed(1)}T`;
    if (abs >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1)}B`;
    if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
    return Number(value).toLocaleString();
}

export function formatTimestamp(ts) {
    try {
        const date = new Date(ts);
        return date.toLocaleString('ja-JP');
    } catch {
        return ts;
    }
}

const localDateTime = new Intl.DateTimeFormat('ja-JP', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
});

export function formatNewsDate(value) {
    if (!value) return '';
    const date = new Date(value);
    return Number.isFinite(date.getTime()) ? localDateTime.format(date) : '';
}

export function oldestStaleData(...sources) {
    return sources.filter(data => data?.stale === true && typeof data.cached_at === 'string')
        .reduce((oldest, data) => !oldest || data.cached_at < oldest.cached_at ? data : oldest, null);
}

export function staleMessage(data) {
    if (!data?.stale || !data.cached_at) return '';
    const date = formatNewsDate(data.cached_at) || data.cached_at;
    return `最新データを取得できなかったため、${date} 時点のデータを表示しています`;
}

export function staleNotice(data) {
    const message = staleMessage(data);
    return message ? `<small class="stale-notice text-muted">${escapeHtml(message)}</small>` : '';
}

export function escapeHtml(value) {
    const entities = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
    return String(value ?? '').replace(/[&<>"']/g, char => entities[char]);
}

// Attribute values must be quoted; event handlers use data attributes/listeners.
export function escapeAttr(value) {
    return escapeHtml(value);
}

export function safeHttpUrl(value) {
    try {
        const url = new URL(String(value ?? ''));
        return ['http:', 'https:'].includes(url.protocol) ? url.href : null;
    } catch {
        return null;
    }
}

export function normalizeTagHue(value) {
    const hue = Number(value);
    return Number.isFinite(hue) ? Math.max(0, Math.min(360, hue)) : 210;
}

Object.assign(window, {
    showToast,
    formatCurrency,
    formatLargeNumber,
    formatAbbreviatedNumber,
    formatTimestamp,
    formatNewsDate,
    escapeHtml,
    escapeAttr,
});
