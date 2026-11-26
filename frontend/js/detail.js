/**
 * Stock Analyzer — Detail view rendering and data loading
 */

import { state } from './state.js';
import { fetchAPI } from './api.js';
import { formatCurrency, formatLargeNumber, formatTimestamp, formatNewsDate, escapeHtml, safeHttpUrl, oldestStaleData, staleMessage, staleNotice } from './utils.js';
import { loadChart, resetPriceChart, updateChartVisibility } from './chart.js';
import { renderComparison, resetComparison } from './comparison.js';
import { normalizeSymbol } from './router.js';
import { renderFinancialHistoryChart, renderDividendHistoryChart } from './financial-chart.js';

let displayedQuote = null;
let detailRequestId = 0;

export function getDetailQuote() {
    return displayedQuote;
}

// ===== Search =====
export function quickSearch(symbol) {
    document.getElementById('search-input').value = symbol;
    searchStock();
}

export function searchStock() {
    const input = document.getElementById('search-input');
    const symbol = normalizeSymbol(input.value);
    const error = document.getElementById('search-error');
    error.textContent = symbol ? '' : '有効なティッカーを入力してください';
    if (symbol) window.switchView('detail', symbol);
}

export async function loadStock(symbol) {
    const requestId = ++detailRequestId;
    const isCurrent = () => requestId === detailRequestId;
    document.getElementById('search-input').value = symbol;
    document.getElementById('search-error').textContent = '';
    state.currentSymbol = symbol;
    displayedQuote = null;
    resetPriceChart();
    resetComparison();
    clearFinancialCharts();
    showSection('loading');
    document.getElementById('loading-text').textContent = `${symbol} のデータを取得中...`;

    try {
        const [price, profile, indicators] = await Promise.all([
            fetchAPI(`/stock/${encodeURIComponent(symbol)}`),
            fetchAPI(`/stock/${encodeURIComponent(symbol)}/profile`),
            fetchAPI(`/stock/${encodeURIComponent(symbol)}/indicators`),
        ]);
        if (!isCurrent()) return;

        renderStockHeader(price, profile);
        renderProfile(profile);
        renderIndicators(indicators);

        showSection('stock-detail');
        refreshDetailHistory();
        loadFinancials(symbol, profile, isCurrent);
        loadNews(symbol, isCurrent);
    } catch (err) {
        if (isCurrent()) showError('データ取得エラー', err.message);
    }
}

export function refreshDetailHistory() {
    if (!displayedQuote) return;
    const { currentSymbol, currentPeriod } = state;
    loadChart(currentSymbol, currentPeriod, displayedQuote.currency);
    renderComparison(currentSymbol, currentPeriod);
}

function clearFinancialCharts() {
    for (const key of ['financialChartInstance', 'dividendChartInstance']) {
        state[key]?.destroy();
        state[key] = null;
    }
}

export function showSection(name) {
    ['welcome-section', 'loading-section', 'error-section', 'stock-detail'].forEach(id => {
        document.getElementById(id).classList.add('hidden');
    });
    if (name === 'loading') document.getElementById('loading-section').classList.remove('hidden');
    else if (name === 'error') document.getElementById('error-section').classList.remove('hidden');
    else if (name === 'stock-detail') document.getElementById('stock-detail').classList.remove('hidden');
    else document.getElementById('welcome-section').classList.remove('hidden');
}

export function showError(title, message) {
    document.getElementById('error-title').textContent = title;
    document.getElementById('error-message').textContent = message;
    showSection('error');
}

// ===== Render: Stock Header =====
export function renderStockHeader(price, profile) {
    document.getElementById('company-name').textContent = profile.name || price.symbol;
    document.getElementById('ticker-badge').textContent = price.symbol;

    const meta = [];
    if (profile.sector) meta.push(profile.sector);
    if (profile.industry) meta.push(profile.industry);
    if (profile.country) meta.push(profile.country);
    document.getElementById('stock-meta').textContent = meta.join(' • ');

    const currency = profile.currency || 'USD';
    displayedQuote = { ...price, currency };
    renderQuotePrice();
    renderQuoteStaleNotice();
    document.getElementById('price-timestamp').textContent = `最終更新: ${formatTimestamp(price.timestamp)}`;
}

function renderQuotePrice() {
    document.getElementById('price-display').textContent = formatCurrency(displayedQuote.price, displayedQuote.currency);
    displayedQuote.renderedRate = state.exchangeRateUSDJPY;
}

function renderQuoteStaleNotice() {
    const notice = document.getElementById('quote-stale');
    const forex = displayedQuote.currency === 'USD' && { stale: true, cached_at: state.exchangeRateCachedAt };
    if (notice) notice.textContent = staleMessage(oldestStaleData(displayedQuote, forex));
}

export function refreshDetailPrices() {
    if (!displayedQuote || document.getElementById('stock-detail').classList.contains('hidden')) return;
    renderQuoteStaleNotice();
    if (displayedQuote.renderedRate === state.exchangeRateUSDJPY) return;
    renderQuotePrice();
    updateChartVisibility();
}

// ===== Render: Profile =====
export function renderProfile(profile) {
    const body = document.getElementById('profile-body');
    let html = '';

    const rows = [
        ['セクター', profile.sector],
        ['業種', profile.industry],
        ['国', profile.country],
        ['時価総額', profile.market_cap ? formatLargeNumber(profile.market_cap, profile.currency) : null],
        ['従業員数', profile.employees ? profile.employees.toLocaleString() + '人' : null],
        ['通貨', profile.currency],
    ];

    rows.forEach(([label, value]) => {
        if (value != null) {
            html += `<div class="data-row"><span class="data-label">${escapeHtml(label)}</span><span class="data-value">${escapeHtml(value)}</span></div>`;
        }
    });

    if (profile.website) {
        const url = safeHttpUrl(profile.website);
        const website = url
            ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" class="profile-site-link">${escapeHtml(new URL(url).hostname)}</a>`
            : escapeHtml(profile.website);
        html += `<div class="data-row"><span class="data-label">ウェブサイト</span><span class="data-value">${website}</span></div>`;
    }

    if (profile.summary) {
        html += `<div class="company-summary">${escapeHtml(profile.summary)}</div>`;
    }

    body.innerHTML = staleNotice(profile) + (html || '<p class="text-muted">データなし</p>');
}

export function getMixIndexRating(value) {
    if (value == null || value <= 0) return null;
    if (value < 22.5) return 'good';
    if (value < 50) return 'neutral';
    return 'bad';
}

export function getRatingSymbol(rating) {
    if (rating === 'good') return '◎';
    if (rating === 'neutral') return '○';
    return '△';
}

export function getMarketCapCategory(marketCap, currency) {
    if (marketCap == null) return null;

    let marketCapUsd = null;
    if (!currency || currency === 'USD') {
        marketCapUsd = marketCap;
    } else if (currency === 'JPY' && state.exchangeRateUSDJPY) {
        marketCapUsd = marketCap / state.exchangeRateUSDJPY;
    }

    if (marketCapUsd == null) return null;
    if (marketCapUsd < 50_000_000) return 'Nano';
    if (marketCapUsd < 300_000_000) return 'Micro';
    if (marketCapUsd < 2_000_000_000) return 'Small';
    if (marketCapUsd < 10_000_000_000) return 'Mid';
    if (marketCapUsd < 200_000_000_000) return 'Large';
    return 'Mega';
}

// ===== Render: Indicators =====
export function renderIndicators(data) {
    const body = document.getElementById('indicators-body');
    let html = '';
    const pbrUnavailable = data.pbr != null && data.pbr <= 0;
    const perUnavailable = data.per != null && data.per <= 0;
    const mixUnavailable = pbrUnavailable || perUnavailable;

    const indicators = [
        ['ROE', data.roe, '%', v => v > 15 ? 'good' : v > 8 ? 'neutral' : 'bad', '高いほど良い'],
        ['ROA', data.roa, '%', v => v > 10 ? 'good' : v > 5 ? 'neutral' : 'bad', '高いほど良い'],
        ['PBR', data.pbr, '倍', v => v < 1 ? 'good' : v < 3 ? 'neutral' : 'bad',
            '正の値は低いほど割安', pbrUnavailable ? '債務超過' : ''],
        ['PER', data.per, '倍', v => v < 15 ? 'good' : v < 30 ? 'neutral' : 'bad',
            '正の値は低いほど割安', perUnavailable ? '赤字' : ''],
        ['EPS', data.eps, '', null, '1株あたり利益'],
        ['ミックス指数', mixUnavailable ? null : data.mix_index, '', getMixIndexRating,
            mixUnavailable ? 'PBRまたはPERが0以下のため算出できません' : 'PER×PBR（22.5未満が割安）',
            mixUnavailable ? '算出不可' : ''],
        ['配当利回り', data.dividend_yield, '%', v => v > 3 ? 'good' : v > 1 ? 'neutral' : 'bad', '高いほど良い'],
        ['利益率', data.profit_margin, '%', v => v > 20 ? 'good' : v > 10 ? 'neutral' : 'bad', '高いほど良い'],
        ['D/Eレシオ', data.debt_to_equity, '', v => v < 50 ? 'good' : v < 150 ? 'neutral' : 'bad', '低いほど財務健全'],
    ];

    indicators.forEach(([label, value, unit, ratingFn, tooltip, note]) => {
        const displayValue = value != null ? `${value}${unit}` : '—';
        const rating = value != null && !note ? ratingFn?.(value) : null;
        const valueClass = rating === 'good' ? 'positive' : rating === 'bad' ? 'negative' : '';
        const badge = rating ? `<span class="indicator-badge ${rating}">${getRatingSymbol(rating)}</span>` : '';
        const noteHtml = note ? `<small class="text-muted">${escapeHtml(note)}</small>` : '';
        html += `
            <div class="data-row" title="${escapeHtml(tooltip)}">
                <span class="data-label">${escapeHtml(label)}</span>
                <span class="data-value ${valueClass}">
                    ${escapeHtml(displayValue)} ${badge}${noteHtml}
                </span>
            </div>`;
    });

    body.innerHTML = staleNotice(data) + html;
}

// ===== Load: Financials =====
export async function loadFinancials(symbol, profile, isCurrent = () => true) {
    const body = document.getElementById('financials-body');
    body.innerHTML = '<div class="skeleton-lines"><div></div><div></div><div></div></div>';

    try {
        const [data, [history, dividends]] = await Promise.all([
            fetchAPI(`/stock/${encodeURIComponent(symbol)}/financials`),
            Promise.allSettled([
                fetchAPI(`/stock/${encodeURIComponent(symbol)}/financials/history?limit=8`),
                fetchAPI(`/stock/${encodeURIComponent(symbol)}/dividends/history?limit=20`),
            ]),
        ]);
        if (!isCurrent()) return;
        let html = staleNotice(data);
        const rows = [
            ['売上高', data.revenue ? formatLargeNumber(data.revenue, profile?.currency) : null],
            ['純利益', data.net_income ? formatLargeNumber(data.net_income, profile?.currency) : null],
            ['EPS', data.eps != null ? data.eps.toFixed(2) : null],
            ['PER', data.pe_ratio != null ? data.pe_ratio.toFixed(2) + '倍' : null],
        ];
        rows.forEach(([label, value]) => {
            html += `<div class="data-row"><span class="data-label">${escapeHtml(label)}</span><span class="data-value">${escapeHtml(value || '—')}</span></div>`;
        });
        html += `
            <div class="financial-history-chart-wrap">
                <div class="mini-chart-header-row">
                    <div class="mini-chart-header">過去の財務推移（年次）</div>
                    <div class="metric-toggle" role="group" aria-label="財務指標の切り替え">
                        <button class="metric-btn" id="metric-revenue" type="button" data-financial-metric="revenue" aria-pressed="false">売上高</button>
                        <button class="metric-btn active" id="metric-net-income" type="button" data-financial-metric="net_income" aria-pressed="true">純利益</button>
                    </div>
                </div>
                <div class="financial-history-chart-container">
                    ${history.status === 'fulfilled' ? '<canvas id="financials-history-chart" role="img" aria-label="年次財務の推移" aria-describedby="financial-chart-summary"></canvas>'
                        : `<p class="text-error">取得失敗: ${escapeHtml(history.reason.message)}</p>`}
                </div>
                <p class="visually-hidden" id="financial-chart-summary"></p>
            </div>
            <div class="financial-history-chart-wrap">
                <div class="mini-chart-header">過去の配当推移（年利%）</div>
                <div class="dividend-history-chart-container" id="dividend-history-chart-container">
                    ${dividends.status === 'fulfilled' ? `<canvas id="dividend-history-chart" role="img" aria-label="年間配当の推移" aria-describedby="dividend-chart-summary"></canvas>
                        <p class="mini-chart-empty hidden" id="dividend-history-empty">配当履歴データがありません</p>`
                        : `<p class="text-error">取得失敗: ${escapeHtml(dividends.reason.message)}</p>`}
                </div>
                <p class="visually-hidden" id="dividend-chart-summary"></p>
            </div>`;
        body.innerHTML = html || '<p class="text-muted">データなし</p>';
        state.currentFinancialHistory = history.value?.history || [];
        state.currentFinancialMetric = 'net_income';
        renderFinancialHistoryChart(state.currentFinancialHistory, state.currentFinancialMetric);
        renderDividendHistoryChart(dividends.value?.history || [], profile?.currency);
    } catch (err) {
        if (!isCurrent()) return;
        clearFinancialCharts();
        body.innerHTML = `<p class="text-error">取得失敗: ${escapeHtml(err.message)}</p>`;
    }
}

// ===== Load: News =====
export async function loadNews(symbol, isCurrent = () => true) {
    const body = document.getElementById('news-body');
    body.innerHTML = '<div class="skeleton-lines"><div></div><div></div><div></div></div>';

    try {
        const data = await fetchAPI(`/stock/${encodeURIComponent(symbol)}/news`);
        if (!isCurrent()) return;
        if (!data.news || data.news.length === 0) {
            body.innerHTML = staleNotice(data) + '<p class="text-muted">ニュースが見つかりませんでした</p>';
            return;
        }
        let html = staleNotice(data) + '<div class="news-list">';
        data.news.forEach(article => {
            const metadata = [formatNewsDate(article.published_at), article.publisher || article.source].filter(Boolean).join(' · ');
            const url = safeHttpUrl(article.url);
            const open = url
                ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" class="news-item">`
                : '<div class="news-item">';
            html += `
                ${open}
                    <div class="news-bullet"></div>
                    <div class="news-item-content">
                        <h4>${escapeHtml(article.title)}</h4>
                        ${metadata ? `<span class="news-date">${escapeHtml(metadata)}</span>` : ''}
                    </div>
                ${url ? '</a>' : '</div>'}`;
        });
        html += '</div>';
        body.innerHTML = html;
    } catch (err) {
        if (isCurrent()) body.innerHTML = `<p class="text-error">取得失敗: ${escapeHtml(err.message)}</p>`;
    }
}

export function bindDetailEvents() {
    document.getElementById('search-btn')?.addEventListener('click', () => {
        searchStock();
    });

    document.getElementById('search-retry-btn')?.addEventListener('click', () => {
        if (state.currentSymbol) loadStock(state.currentSymbol);
    });

    document.addEventListener('click', e => {
        const quickSearchEl = e.target.closest('[data-quick-search]');
        if (quickSearchEl) {
            quickSearch(quickSearchEl.dataset.quickSearch);
        }
    });
}
