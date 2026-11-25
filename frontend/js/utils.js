/**
 * Stock Analyzer — Shared UI and formatting helpers
 */

import { state } from './state.js';

export function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;

    const icons = { success: '✓', error: '✕', warning: '⚠', info: 'ℹ' };
    toast.innerHTML = `<span class="toast-icon">${escapeHtml(icons[type] || 'ℹ')}</span><span class="toast-message">${escapeHtml(message)}</span>`;

    container.appendChild(toast);
    setTimeout(() => {
        toast.classList.add('toast-hide');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
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

export function formatNewsDate(dateStr) {
    try {
        const date = new Date(dateStr);
        if (isNaN(date.getTime())) return dateStr;
        return date.toLocaleDateString('ja-JP', { year: 'numeric', month: 'short', day: 'numeric' });
    } catch {
        return dateStr;
    }
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
