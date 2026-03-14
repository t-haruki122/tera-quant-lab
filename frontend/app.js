/**
 * Stock Analyzer — メインアプリケーションロジック
 */

// ===== State =====
const API_BASE = '';
const cache = new Map();        // symbol -> { data, timestamp }
const CACHE_TTL = 5 * 60 * 1000; // 5分（フロント側キャッシュ）

let currentSymbol = null;
let priceChart = null;
let currentPeriod = '3mo';

// リストビュー
let listItems = [];             // [{ symbol, price, profile, indicators }]
let sortKey = 'symbol';
let sortAsc = true;

// ===== View Switch =====
function switchView(view) {
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));

    if (view === 'detail') {
        document.getElementById('detail-view').classList.add('active');
        document.getElementById('btn-detail-view').classList.add('active');
    } else {
        document.getElementById('list-view').classList.add('active');
        document.getElementById('btn-list-view').classList.add('active');
    }
}

// ===== API Helper =====
async function fetchAPI(endpoint) {
    const cacheKey = endpoint;
    const cached = cache.get(cacheKey);
    if (cached && Date.now() - cached.timestamp < CACHE_TTL) {
        return cached.data;
    }

    const res = await fetch(`${API_BASE}${endpoint}`);
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Unknown error' }));
        throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    cache.set(cacheKey, { data, timestamp: Date.now() });
    return data;
}

// ===== Search =====
function quickSearch(symbol) {
    document.getElementById('search-input').value = symbol;
    searchStock();
}

async function searchStock() {
    const input = document.getElementById('search-input');
    const symbol = input.value.trim().toUpperCase();
    if (!symbol) return;

    currentSymbol = symbol;
    showSection('loading');
    document.getElementById('loading-text').textContent = `${symbol} のデータを取得中...`;

    try {
        // 並列でデータ取得
        const [price, profile, indicators] = await Promise.all([
            fetchAPI(`/stock/${symbol}`),
            fetchAPI(`/stock/${symbol}/profile`),
            fetchAPI(`/stock/${symbol}/indicators`),
        ]);

        // 表示
        renderStockHeader(price, profile);
        renderProfile(profile);
        renderIndicators(indicators);

        // 非同期でチャート・財務・ニュース取得
        showSection('stock-detail');

        loadChart(symbol, currentPeriod);
        loadFinancials(symbol);
        loadNews(symbol);

    } catch (err) {
        showError('データ取得エラー', err.message);
    }
}

function showSection(name) {
    ['welcome-section', 'loading-section', 'error-section', 'stock-detail'].forEach(id => {
        document.getElementById(id).classList.add('hidden');
    });
    if (name === 'loading') document.getElementById('loading-section').classList.remove('hidden');
    else if (name === 'error') document.getElementById('error-section').classList.remove('hidden');
    else if (name === 'stock-detail') document.getElementById('stock-detail').classList.remove('hidden');
    else document.getElementById('welcome-section').classList.remove('hidden');
}

function showError(title, message) {
    document.getElementById('error-title').textContent = title;
    document.getElementById('error-message').textContent = message;
    showSection('error');
}

// ===== Render: Stock Header =====
function renderStockHeader(price, profile) {
    document.getElementById('company-name').textContent = profile.name || price.symbol;
    document.getElementById('ticker-badge').textContent = price.symbol;

    const meta = [];
    if (profile.sector) meta.push(profile.sector);
    if (profile.industry) meta.push(profile.industry);
    if (profile.country) meta.push(profile.country);
    document.getElementById('stock-meta').textContent = meta.join(' • ');

    const currency = profile.currency || 'USD';
    document.getElementById('price-display').textContent =
        formatCurrency(price.price, currency);
    document.getElementById('price-timestamp').textContent =
        `最終更新: ${formatTimestamp(price.timestamp)}`;
}

// ===== Render: Profile =====
function renderProfile(profile) {
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

// ===== Render: Indicators =====
function renderIndicators(data) {
    const body = document.getElementById('indicators-body');
    let html = '';

    const indicators = [
        ['ROE', data.roe, '%', v => v > 15 ? 'good' : v > 8 ? 'neutral' : 'bad', '高いほど良い'],
        ['ROA', data.roa, '%', v => v > 10 ? 'good' : v > 5 ? 'neutral' : 'bad', '高いほど良い'],
        ['PBR', data.pbr, '倍', v => v < 1 ? 'good' : v < 3 ? 'neutral' : 'bad', '低いほど割安'],
        ['PER', data.per, '倍', v => v < 15 ? 'good' : v < 30 ? 'neutral' : 'bad', '低いほど割安'],
        ['EPS', data.eps, '', null, '1株あたり利益'],
        ['ミックス指数', data.mix_index, '', v => v < 22.5 ? 'good' : v < 50 ? 'neutral' : 'bad', 'PER×PBR（22.5以下が割安）'],
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
async function loadFinancials(symbol) {
    const body = document.getElementById('financials-body');
    body.innerHTML = '<div class="skeleton-lines"><div></div><div></div><div></div></div>';

    try {
        const data = await fetchAPI(`/stock/${symbol}/financials`);
        let html = '';

        const rows = [
            ['売上高', data.revenue ? formatLargeNumber(data.revenue) : null],
            ['純利益', data.net_income ? formatLargeNumber(data.net_income) : null],
            ['EPS', data.eps != null ? data.eps.toFixed(2) : null],
            ['PER', data.pe_ratio != null ? data.pe_ratio.toFixed(2) + '倍' : null],
        ];

        rows.forEach(([label, value]) => {
            html += `<div class="data-row"><span class="data-label">${label}</span><span class="data-value">${value || '—'}</span></div>`;
        });

        body.innerHTML = html || '<p style="color:var(--text-muted)">データなし</p>';
    } catch (err) {
        body.innerHTML = `<p style="color:var(--accent-rose)">取得失敗: ${err.message}</p>`;
    }
}

// ===== Load: News =====
async function loadNews(symbol) {
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

// ===== Chart =====
async function loadChart(symbol, period) {
    const periodMap = {
        '1mo': { months: 1 },
        '3mo': { months: 3 },
        '6mo': { months: 6 },
        '1y':  { months: 12 },
        '2y':  { months: 24 },
    };

    const p = periodMap[period] || periodMap['3mo'];
    const endDate = new Date();
    const startDate = new Date();
    startDate.setMonth(startDate.getMonth() - p.months);

    const startStr = startDate.toISOString().split('T')[0];
    const endStr = endDate.toISOString().split('T')[0];
    const interval = p.months > 12 ? '1wk' : '1d';

    try {
        const data = await fetchAPI(`/stock/${symbol}/history?start_date=${startStr}&end_date=${endStr}&interval=${interval}`);
        renderChart(data.history);
    } catch (err) {
        console.error('Chart load error:', err);
    }
}

function renderChart(history) {
    const ctx = document.getElementById('price-chart').getContext('2d');

    if (priceChart) {
        priceChart.destroy();
    }

    const labels = history.map(h => h.date);
    const closes = history.map(h => h.close);

    // 色を値動きに応じて変更
    const isUp = closes.length >= 2 && closes[closes.length - 1] >= closes[0];
    const lineColor = isUp ? '#10b981' : '#f43f5e';
    const fillColor = isUp ? 'rgba(16, 185, 129, 0.08)' : 'rgba(244, 63, 94, 0.08)';

    priceChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: '終値',
                data: closes,
                borderColor: lineColor,
                backgroundColor: fillColor,
                fill: true,
                tension: 0.3,
                borderWidth: 2,
                pointRadius: 0,
                pointHitRadius: 10,
                pointHoverRadius: 5,
                pointHoverBackgroundColor: lineColor,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                mode: 'index',
                intersect: false,
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: 'rgba(17, 24, 39, 0.95)',
                    titleColor: '#94a3b8',
                    bodyColor: '#f1f5f9',
                    borderColor: 'rgba(255,255,255,0.1)',
                    borderWidth: 1,
                    padding: 12,
                    cornerRadius: 8,
                    titleFont: { size: 12 },
                    bodyFont: { size: 14, weight: '600' },
                    callbacks: {
                        label: ctx => `  ${ctx.parsed.y.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
                    }
                }
            },
            scales: {
                x: {
                    grid: { color: 'rgba(255,255,255,0.04)', drawBorder: false },
                    ticks: {
                        color: '#64748b',
                        font: { size: 11 },
                        maxTicksLimit: 8,
                        maxRotation: 0,
                    },
                    border: { display: false },
                },
                y: {
                    position: 'right',
                    grid: { color: 'rgba(255,255,255,0.04)', drawBorder: false },
                    ticks: {
                        color: '#64748b',
                        font: { size: 11 },
                        callback: v => v.toLocaleString(),
                    },
                    border: { display: false },
                }
            }
        }
    });
}

function changePeriod(period) {
    currentPeriod = period;
    document.querySelectorAll('.period-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.period === period);
    });
    if (currentSymbol) {
        loadChart(currentSymbol, period);
    }
}

// ===== List View =====
function quickAddToList(symbol) {
    document.getElementById('list-add-input').value = symbol;
    addToList();
}

async function addToList() {
    const input = document.getElementById('list-add-input');
    const symbol = input.value.trim().toUpperCase();
    if (!symbol) return;
    input.value = '';

    // 重複チェック
    if (listItems.find(item => item.symbol === symbol)) return;

    // ローディングアイテム追加
    const item = { symbol, loading: true };
    listItems.push(item);
    renderListTable();

    try {
        const [price, profile, indicators] = await Promise.all([
            fetchAPI(`/stock/${symbol}`),
            fetchAPI(`/stock/${symbol}/profile`),
            fetchAPI(`/stock/${symbol}/indicators`),
        ]);

        const idx = listItems.findIndex(i => i.symbol === symbol);
        if (idx >= 0) {
            listItems[idx] = {
                symbol,
                loading: false,
                name: profile.name,
                price: price.price,
                currency: profile.currency,
                market_cap: profile.market_cap,
                per: indicators.per,
                pbr: indicators.pbr,
                roe: indicators.roe,
                mix_index: indicators.mix_index,
                dividend_yield: indicators.dividend_yield,
            };
            renderListTable();
        }
    } catch (err) {
        // エラー時は削除
        listItems = listItems.filter(i => i.symbol !== symbol);
        renderListTable();
        console.error(`Failed to load ${symbol}:`, err);
    }
}

function removeFromList(symbol) {
    listItems = listItems.filter(i => i.symbol !== symbol);
    renderListTable();
}

function sortTable(key) {
    if (sortKey === key) {
        sortAsc = !sortAsc;
    } else {
        sortKey = key;
        sortAsc = true;
    }
    renderListTable();
}

function renderListTable() {
    const emptyEl = document.getElementById('list-empty');
    const tableEl = document.getElementById('list-table-section');

    if (listItems.length === 0) {
        emptyEl.classList.remove('hidden');
        tableEl.classList.add('hidden');
        return;
    }

    emptyEl.classList.add('hidden');
    tableEl.classList.remove('hidden');

    // ソート
    const sorted = [...listItems].sort((a, b) => {
        if (a.loading) return 1;
        if (b.loading) return -1;
        let va = a[sortKey];
        let vb = b[sortKey];
        if (va == null) return 1;
        if (vb == null) return -1;
        if (typeof va === 'string') {
            return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
        }
        return sortAsc ? va - vb : vb - va;
    });

    // ヘッダーの矢印を更新
    document.querySelectorAll('#comparison-table th.sortable').forEach(th => {
        const key = th.dataset.sort;
        th.classList.toggle('sort-active', key === sortKey);
        const arrow = th.querySelector('.sort-arrow');
        if (key === sortKey) {
            arrow.textContent = sortAsc ? ' ▲' : ' ▼';
        } else {
            arrow.textContent = '';
        }
    });

    // テーブル描画
    const tbody = document.getElementById('comparison-tbody');
    let html = '';

    sorted.forEach(item => {
        if (item.loading) {
            html += `
                <tr class="row-loading">
                    <td class="table-ticker">${item.symbol}</td>
                    <td colspan="8" style="color:var(--text-muted)">読み込み中...</td>
                    <td><button class="btn-remove" onclick="event.stopPropagation(); removeFromList('${item.symbol}')">削除</button></td>
                </tr>`;
            return;
        }

        html += `
            <tr onclick="goToDetail('${item.symbol}')">
                <td class="table-ticker">${item.symbol}</td>
                <td class="table-company">${escapeHtml(item.name || '—')}</td>
                <td class="numeric">${item.price != null ? formatCurrency(item.price, item.currency) : '—'}</td>
                <td class="numeric">${item.market_cap != null ? formatLargeNumber(item.market_cap) : '—'}</td>
                <td class="numeric">${item.per != null ? item.per.toFixed(2) : '—'}</td>
                <td class="numeric">${item.pbr != null ? item.pbr.toFixed(2) : '—'}</td>
                <td class="numeric">${item.roe != null ? item.roe.toFixed(2) + '%' : '—'}</td>
                <td class="numeric">${item.mix_index != null ? item.mix_index.toFixed(2) : '—'}</td>
                <td class="numeric">${item.dividend_yield != null ? item.dividend_yield.toFixed(2) + '%' : '—'}</td>
                <td><button class="btn-remove" onclick="event.stopPropagation(); removeFromList('${item.symbol}')">削除</button></td>
            </tr>`;
    });

    tbody.innerHTML = html;
}

function goToDetail(symbol) {
    document.getElementById('search-input').value = symbol;
    switchView('detail');
    searchStock();
}

// ===== Formatting Utilities =====
function formatCurrency(value, currency) {
    try {
        const locale = currency === 'JPY' ? 'ja-JP' : 'en-US';
        return new Intl.NumberFormat(locale, {
            style: 'currency',
            currency: currency || 'USD',
            minimumFractionDigits: currency === 'JPY' ? 0 : 2,
            maximumFractionDigits: currency === 'JPY' ? 0 : 2,
        }).format(value);
    } catch {
        return value.toLocaleString();
    }
}

function formatLargeNumber(value, currency) {
    const abs = Math.abs(value);
    if (abs >= 1e12) return (value / 1e12).toFixed(2) + '兆';
    if (abs >= 1e8)  return (value / 1e8).toFixed(2) + '億';
    if (abs >= 1e6)  return (value / 1e6).toFixed(2) + 'M';
    if (abs >= 1e3)  return (value / 1e3).toFixed(1) + 'K';
    return value.toLocaleString();
}

function formatTimestamp(ts) {
    try {
        const date = new Date(ts);
        return date.toLocaleString('ja-JP');
    } catch {
        return ts;
    }
}

function formatNewsDate(dateStr) {
    try {
        const date = new Date(dateStr);
        if (isNaN(date.getTime())) return dateStr;
        return date.toLocaleDateString('ja-JP', {
            year: 'numeric', month: 'short', day: 'numeric'
        });
    } catch {
        return dateStr;
    }
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// ===== Event Listeners =====
document.getElementById('search-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') searchStock();
});

document.getElementById('list-add-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') addToList();
});
