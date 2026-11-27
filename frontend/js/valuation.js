import { fetchAPI } from './api.js';
import { normalizeSymbol, serializeRoute } from './router.js';
import { escapeHtml } from './utils.js';

export const MIX_INDEX_HELP = 'PER×PBR。ROEが高い企業ほど高く出やすく、業種で水準が異なります';

const byId = id => document.getElementById(id);
const sourceLabels = { jpx33: 'JPX 33業種', yahoo_sector: 'Yahoo Finance セクター', yahoo_industry: 'Yahoo Finance 業種' };
const statementSources = { jquants: 'J-Quants', yfinance: 'Yahoo Finance' };
const percentageMetrics = new Set(['roe', 'operating_margin', 'profit_margin', 'equity_ratio', 'sales_growth', 'dividend_yield']);
const format = (value, unit = '') => Number.isFinite(value) ? `${value.toFixed(2)}${unit}` : '—';
const notesMarkup = notes => (notes ?? []).map(note => `<p class="valuation-note text-muted">${escapeHtml(note)}</p>`).join('');
const metricUnit = key => percentageMetrics.has(key) ? '%' : ['per', 'pbr'].includes(key) ? '倍' : '';
const priceMetrics = ['PER', 'PBR', 'ミックス指数', '配当利回り'];

function peerRow(metric) {
    const unit = metricUnit(metric.key);
    const count = Number(metric.count);
    const rank = Number.isInteger(metric.rank) ? `${metric.rank}位 / ${count}社`
        : count < 5 ? '比較対象が少ないため順位なし' : '—';
    const pct = Number.isFinite(metric.percentile) ? Math.min(100, Math.max(0, metric.percentile)) : null;
    const position = pct === null ? '' : `<span class="peer-percentile">
        <meter min="0" max="100" value="${pct}" aria-hidden="true"></meter>
        <small>パーセンタイル ${pct} / 100</small></span>`;
    return `<tr><th scope="row">${escapeHtml(metric.label)}</th><td>${format(metric.value, unit)}</td>
        <td>${format(metric.median, unit)}</td><td>${rank}${position}</td></tr>`;
}

function peerCompanyRow(company) {
    const symbol = normalizeSymbol(company.symbol);
    const label = escapeHtml([company.symbol, company.name].filter(Boolean).join(' '));
    const name = symbol ? `<a href="${escapeHtml(serializeRoute({ view: 'detail', symbol }))}">${label}</a>` : label;
    return `<tr><th scope="row">${name}</th><td>${format(company.roe, '%')}</td>
        <td>${format(company.per, '倍')}</td><td>${format(company.pbr, '倍')}</td></tr>`;
}

export function renderPeers(data, priceState = '') {
    const { sector_name: sector, sector_source: source, peer_count: count, as_of: asOf, metrics, peers } = data;
    const body = byId('peers-body');
    if (!sector) {
        body.innerHTML = '<p class="text-muted">同業種の比較データがありません</p>' + notesMarkup(data.notes);
        return;
    }
    const loadingRows = priceState ? priceMetrics.map(label => `<tr><th scope="row">${label}</th>
        <td colspan="3" class="text-muted">${escapeHtml(priceState)}</td></tr>`).join('') : '';
    const companies = !priceState && peers?.length ? `<div class="valuation-table-wrap"><table class="valuation-table">
        <caption>比較銘柄（時価総額順）</caption><thead><tr><th scope="col">銘柄</th><th scope="col">ROE</th>
        <th scope="col">PER</th><th scope="col">PBR</th></tr></thead>
        <tbody>${peers.map(peerCompanyRow).join('')}</tbody></table></div>` : '';
    body.innerHTML = `<p>${escapeHtml(sector)}（${escapeHtml(sourceLabels[source] ?? source)}） · ${escapeHtml(count)}社
        ${asOf ? ` · データ基準日 ${escapeHtml(asOf)}` : ''}</p>
        <div class="valuation-table-wrap"><table class="valuation-table">
        <caption class="visually-hidden">指標別の業種内比較</caption>
        <thead><tr><th scope="col">指標</th><th scope="col">この銘柄</th><th scope="col">業種中央値</th>
        <th scope="col">順位（数値の高い順）</th></tr></thead>
        <tbody>${(metrics ?? []).map(peerRow).join('')}${loadingRows}</tbody></table></div>
        ${companies}${notesMarkup(data.notes)}`;
}

export async function loadPeers(path, isCurrent, cachedProfileReady = Promise.resolve()) {
    const status = byId('peers-status');
    status.textContent = '同業種比較を読み込み中…';
    byId('peers-body').innerHTML = '';
    let base;
    try {
        base = await fetchAPI(`${path}/peers?prices=false`);
        if (!isCurrent()) return;
        if (base.sector_source === 'jpx33') {
            renderPeers(base, '株価を取得中…');
            status.textContent = '株価を使う指標を読み込み中…';
            const complete = await fetchAPI(`${path}/peers`);
            if (!isCurrent()) return;
            renderPeers(complete);
            status.textContent = '同業種比較を更新しました';
            return;
        }
        renderPeers(base);
        if (!base.metrics?.some(metric => metric.value != null)) {
            await cachedProfileReady;
            if (!isCurrent()) return;
            const refreshed = await fetchAPI(`${path}/peers`);
            if (!isCurrent()) return;
            renderPeers(refreshed);
        }
        status.textContent = '';
    } catch (error) {
        if (!isCurrent()) return;
        status.textContent = `比較データの取得に失敗しました（${error.message}）`;
        if (base?.sector_source === 'jpx33') renderPeers(base, '取得できませんでした');
    }
}

const valuationSeries = [
    { key: 'per', label: 'PER', invalid: '赤字', amount: 'eps', color: '#60a5fa' },
    { key: 'pbr', label: 'PBR', invalid: '債務超過', amount: 'bps', color: '#fbbf24' },
];
const charts = [];

export function clearValuationCharts() {
    for (const chart of charts.splice(0)) chart.destroy();
}

function yearValue(point, series) {
    return point[series.amount] != null && point[series.amount] <= 0 ? series.invalid : format(point[series.key], '倍');
}

function chartConfig(points, series, band, current) {
    const { key, label, color } = series;
    const labels = [...points.map(p => p.period_end), '現在'];
    const present = band ? [['最小', band.min], ['最大', band.max]] : [];
    return {
        type: 'line',
        data: { labels, datasets: [
            { label, data: [...points.map(point => point[key]), null], borderColor: color, backgroundColor: color,
                pointRadius: 3, borderWidth: 2 },
            { label: `${label}（現在）`, data: [...points.map(() => null), current], borderColor: color,
                backgroundColor: color, pointStyle: 'rectRot', pointRadius: 6 },
            ...present.map(([name, value]) => ({ label: `${label} ${name}`, data: labels.map(() => value),
                borderColor: '#94a3b8', borderDash: [5, 4], borderWidth: 1, pointRadius: 0 })),
        ] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } },
            scales: { x: { ticks: { maxTicksLimit: 4, maxRotation: 0 } }, y: { ticks: { callback: v => `${v}倍` } } } },
    };
}

export function renderValuationHistory(data) {
    clearValuationCharts();
    const points = data.points ?? [];
    const chartable = points.filter(point => Number.isFinite(point.per) || Number.isFinite(point.pbr)).length >= 2;
    const summaries = valuationSeries.map(series => {
        const band = data.band?.[series.key];
        const current = format(data.current?.[series.key], '倍');
        const range = band ? `、最小 ${format(band.min, '倍')}、中央値 ${format(band.median, '倍')}、最大 ${format(band.max, '倍')}` : '';
        const { key, label } = series;
        const yearly = points.map(p => `${p.period_end} ${yearValue(p, series)}`).join('、');
        return `<section><h4>${label}</h4><p>${escapeHtml(`現在 ${current}${range}`)}</p>
            ${chartable ? `<div class="valuation-plot"><canvas id="valuation-${key}-chart" role="img" aria-label="${label}の推移"
            aria-describedby="valuation-${key}-summary"></canvas></div>` : ''}
            <p id="valuation-${key}-summary" class="visually-hidden">${escapeHtml(`現在 ${current}${range}。${yearly}`)}</p></section>`;
    }).join('');
    const details = points.length ? `<details><summary>年度別の数値と出典</summary><div class="valuation-table-wrap">
        <table class="valuation-table"><caption class="visually-hidden">年度別の数値と出典</caption>
        <thead><tr><th scope="col">決算期末</th><th scope="col">PER</th><th scope="col">PBR</th><th scope="col">出典</th></tr></thead>
        <tbody>${points.map(p => `<tr><th scope="row">${escapeHtml(p.period_end)}</th>
        ${valuationSeries.map(series => `<td>${yearValue(p, series)}</td>`).join('')}
        <td>${escapeHtml(statementSources[p.source] ?? p.source)}</td></tr>`).join('')}</tbody></table></div></details>` : '';
    byId('valuation-history-body').innerHTML = `${chartable ? '' : '<p class="text-muted">グラフの表示には2期分のデータが必要です。</p>'}
        <div class="valuation-charts">${summaries}</div>${details}${notesMarkup(data.notes)}`;
    if (chartable && typeof Chart === 'function') {
        for (const series of valuationSeries) {
            const context = byId(`valuation-${series.key}-chart`).getContext('2d');
            if (context) charts.push(new Chart(context, chartConfig(points, series, data.band?.[series.key], data.current?.[series.key])));
        }
    }
}
