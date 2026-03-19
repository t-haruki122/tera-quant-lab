/**
 * Stock Analyzer — Detail view rendering and data loading
 */

import { state } from './state.js';
import { fetchAPI } from './api.js';
import { formatCurrency, formatLargeNumber, formatTimestamp, formatNewsDate, escapeHtml } from './utils.js';
import { loadChart, clearPriceAndRsiCharts } from './chart.js';
import { renderFinancialHistoryChart, renderDividendHistoryChart } from './financial-chart.js';

// ===== Search =====
export function quickSearch(symbol) {
    document.getElementById('search-input').value = symbol;
    searchStock();
}

export async function searchStock() {
    const input = document.getElementById('search-input');
    const symbol = input.value.trim().toUpperCase();
    if (!symbol) return;

    state.currentSymbol = symbol;
    state.currentChartHistory = [];
    state.currentChartCurrency = null;
    clearPriceAndRsiCharts();
    showSection('loading');
    document.getElementById('loading-text').textContent = `${symbol} のデータを取得中...`;

    try {
        const [price, profile, indicators] = await Promise.all([
            fetchAPI(`/stock/${symbol}`),
            fetchAPI(`/stock/${symbol}/profile`),
            fetchAPI(`/stock/${symbol}/indicators`),
        ]);

        renderStockHeader(price, profile);
        renderProfile(profile);
        renderIndicators(indicators);

        showSection('stock-detail');
        loadChart(symbol, state.currentPeriod, profile.currency);
        loadFinancials(symbol, profile);
        loadNews(symbol);
    } catch (err) {
        showError('データ取得エラー', err.message);
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
    document.getElementById('price-display').textContent = formatCurrency(price.price, currency);
    document.getElementById('price-timestamp').textContent = `最終更新: ${formatTimestamp(price.timestamp)}`;
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

    if (profile.website) {
        rows.push(['ウェブサイト', `<a href="${profile.website}" target="_blank" style="color:var(--accent-blue);text-decoration:none;">${new URL(profile.website).hostname}</a>`]);
    }

    rows.forEach(([label, value]) => {
        if (value != null) {
            html += `<div class="data-row"><span class="data-label">${label}</span><span class="data-value">${value}</span></div>`;
        }
    });

    if (profile.summary) {
        html += `<div class="company-summary">${profile.summary}</div>`;
    }

    body.innerHTML = html || '<p style="color:var(--text-muted)">データなし</p>';
}

export function getMixIndexRating(value) {
    if (value == null) return null;
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

    const indicators = [
        ['ROE', data.roe, '%', v => v > 15 ? 'good' : v > 8 ? 'neutral' : 'bad', '高いほど良い'],
        ['ROA', data.roa, '%', v => v > 10 ? 'good' : v > 5 ? 'neutral' : 'bad', '高いほど良い'],
        ['PBR', data.pbr, '倍', v => v < 1 ? 'good' : v < 3 ? 'neutral' : 'bad', '低いほど割安'],
        ['PER', data.per, '倍', v => v < 15 ? 'good' : v < 30 ? 'neutral' : 'bad', '低いほど割安'],
        ['EPS', data.eps, '', null, '1株あたり利益'],
        ['ミックス指数', data.mix_index, '', v => getMixIndexRating(v), 'PER×PBR（22.5以下が割安）'],
        ['配当利回り', data.dividend_yield, '%', v => v > 3 ? 'good' : v > 1 ? 'neutral' : 'bad', '高いほど良い'],
        ['利益率', data.profit_margin, '%', v => v > 20 ? 'good' : v > 10 ? 'neutral' : 'bad', '高いほど良い'],
        ['D/Eレシオ', data.debt_to_equity, '', v => v < 50 ? 'good' : v < 150 ? 'neutral' : 'bad', '低いほど財務健全'],
    ];

    indicators.forEach(([label, value, unit, ratingFn, tooltip]) => {
        const displayValue = value != null ? `${value}${unit}` : '—';
        let badge = '';
        if (value != null && ratingFn) {
            const rating = ratingFn(value);
            const ratingLabel = rating === 'good' ? '◎' : rating === 'neutral' ? '○' : '△';
            badge = `<span class="indicator-badge ${rating}">${ratingLabel}</span>`;
        }
        html += `
            <div class="data-row" title="${tooltip || ''}">
                <span class="data-label">${label}</span>
                <span class="data-value ${value != null && ratingFn ? ratingFn(value) === 'good' ? 'positive' : ratingFn(value) === 'bad' ? 'negative' : '' : ''}">
                    ${displayValue} ${badge}
                </span>
            </div>`;
    });

    body.innerHTML = html;
}

// ===== Load: Financials =====
export async function loadFinancials(symbol, profile) {
    const body = document.getElementById('financials-body');
    body.innerHTML = '<div class="skeleton-lines"><div></div><div></div><div></div></div>';

    try {
        const [data, historyData, dividendHistoryData] = await Promise.all([
            fetchAPI(`/stock/${symbol}/financials`),
            fetchAPI(`/stock/${symbol}/financials/history?limit=8`),
            fetchAPI(`/stock/${symbol}/dividends/history?limit=20`),
        ]);
        let html = '';
        const rows = [
            ['売上高', data.revenue ? formatLargeNumber(data.revenue, profile?.currency) : null],
            ['純利益', data.net_income ? formatLargeNumber(data.net_income, profile?.currency) : null],
            ['EPS', data.eps != null ? data.eps.toFixed(2) : null],
            ['PER', data.pe_ratio != null ? data.pe_ratio.toFixed(2) + '倍' : null],
        ];
        rows.forEach(([label, value]) => {
            html += `<div class="data-row"><span class="data-label">${label}</span><span class="data-value">${value || '—'}</span></div>`;
        });
        html += `
            <div class="financial-history-chart-wrap">
                <div class="mini-chart-header-row">
                    <div class="mini-chart-header">過去の財務推移（年次）</div>
                    <div class="metric-toggle" role="group" aria-label="財務指標の切り替え">
                        <button class="metric-btn" id="metric-revenue" type="button" data-financial-metric="revenue">売上高</button>
                        <button class="metric-btn active" id="metric-net-income" type="button" data-financial-metric="net_income">純利益</button>
                    </div>
                </div>
                <div class="financial-history-chart-container">
                    <canvas id="financials-history-chart"></canvas>
                </div>
            </div>
            <div class="financial-history-chart-wrap">
                <div class="mini-chart-header">過去の配当推移（年利%）</div>
                <div class="dividend-history-chart-container" id="dividend-history-chart-container">
                    <canvas id="dividend-history-chart"></canvas>
                    <p class="mini-chart-empty hidden" id="dividend-history-empty">配当履歴データがありません</p>
                </div>
            </div>`;
        body.innerHTML = html || '<p style="color:var(--text-muted)">データなし</p>';
        state.currentFinancialHistory = historyData?.history || [];
        state.currentFinancialMetric = 'net_income';
        renderFinancialHistoryChart(state.currentFinancialHistory, state.currentFinancialMetric);
        renderDividendHistoryChart(dividendHistoryData?.history || [], profile?.currency);
    } catch (err) {
        if (state.financialChartInstance) {
            state.financialChartInstance.destroy();
            state.financialChartInstance = null;
        }
        if (state.dividendChartInstance) {
            state.dividendChartInstance.destroy();
            state.dividendChartInstance = null;
        }
        body.innerHTML = `<p style="color:var(--accent-rose)">取得失敗: ${err.message}</p>`;
    }
}

// ===== Load: News =====
export async function loadNews(symbol) {
    const body = document.getElementById('news-body');
    body.innerHTML = '<div class="skeleton-lines"><div></div><div></div><div></div></div>';

    try {
        const data = await fetchAPI(`/stock/${symbol}/news`);
        if (!data.news || data.news.length === 0) {
            body.innerHTML = '<p style="color:var(--text-muted)">ニュースが見つかりませんでした</p>';
            return;
        }
        let html = '<div class="news-list">';
        data.news.forEach(article => {
            const dateStr = article.published_at ? formatNewsDate(article.published_at) : '';
            html += `
                <a href="${article.url}" target="_blank" rel="noopener" class="news-item">
                    <div class="news-bullet"></div>
                    <div class="news-item-content">
                        <h4>${escapeHtml(article.title)}</h4>
                        ${dateStr ? `<span class="news-date">${dateStr}</span>` : ''}
                    </div>
                </a>`;
        });
        html += '</div>';
        body.innerHTML = html;
    } catch (err) {
        body.innerHTML = `<p style="color:var(--accent-rose)">取得失敗: ${err.message}</p>`;
    }
}

export function bindDetailEvents() {
    document.getElementById('search-btn')?.addEventListener('click', () => {
        searchStock();
    });

    document.getElementById('search-retry-btn')?.addEventListener('click', () => {
        searchStock();
    });

    document.addEventListener('click', e => {
        const quickSearchEl = e.target.closest('[data-quick-search]');
        if (quickSearchEl) {
            quickSearch(quickSearchEl.dataset.quickSearch);
        }
    });
}
