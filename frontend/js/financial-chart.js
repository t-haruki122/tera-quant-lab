/**
 * Stock Analyzer — Financial and dividend chart rendering
 */

import { state } from './state.js';
import { formatLargeNumber, formatAbbreviatedNumber } from './utils.js';

export function changeFinancialMetric(metric) {
    state.currentFinancialMetric = metric;
    document.getElementById('metric-revenue')?.classList.toggle('active', metric === 'revenue');
    document.getElementById('metric-net-income')?.classList.toggle('active', metric === 'net_income');
    renderFinancialHistoryChart(state.currentFinancialHistory, state.currentFinancialMetric);
}

export function renderFinancialHistoryChart(history, metric = 'net_income') {
    if (state.financialChartInstance) {
        state.financialChartInstance.destroy();
        state.financialChartInstance = null;
    }

    const canvas = document.getElementById('financials-history-chart');
    if (!canvas || !history || history.length === 0) {
        return;
    }

    const labels = history.map(item => item.period);
    const isRevenue = metric === 'revenue';
    const series = history.map(item => item[metric]);
    const label = isRevenue ? '売上高' : '純利益';
    const barColor = isRevenue
        ? 'rgba(59, 130, 246, 0.65)'
        : series.map(v => (v != null && v < 0 ? 'rgba(239, 68, 68, 0.75)' : 'rgba(16, 185, 129, 0.65)'));
    const borderColor = isRevenue
        ? '#3b82f6'
        : series.map(v => (v != null && v < 0 ? '#ef4444' : '#10b981'));

    state.financialChartInstance = new Chart(canvas.getContext('2d'), {
        type: 'bar',
        data: {
            labels,
            datasets: [{
                label,
                data: series,
                backgroundColor: barColor,
                borderColor,
                borderWidth: 1,
                borderRadius: 6,
                maxBarThickness: 44,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: 'rgba(17, 24, 39, 0.95)',
                    titleColor: '#94a3b8',
                    bodyColor: '#f1f5f9',
                    borderColor: 'rgba(255,255,255,0.1)',
                    borderWidth: 1,
                    callbacks: {
                        label: ctx => `  ${label}: ${formatLargeNumber(ctx.parsed.y)}`,
                    },
                },
            },
            scales: {
                x: {
                    ticks: { color: '#64748b', font: { size: 11 } },
                    grid: { color: 'rgba(255,255,255,0.04)', drawBorder: false },
                    border: { display: false },
                },
                y: {
                    ticks: {
                        color: '#64748b',
                        font: { size: 11 },
                        callback: value => formatAbbreviatedNumber(value),
                    },
                    grid: { color: 'rgba(255,255,255,0.04)', drawBorder: false },
                    border: { display: false },
                },
            },
        },
    });
}

export function renderDividendHistoryChart(history, currency) {
    if (state.dividendChartInstance) {
        state.dividendChartInstance.destroy();
        state.dividendChartInstance = null;
    }

    const canvas = document.getElementById('dividend-history-chart');
    const emptyEl = document.getElementById('dividend-history-empty');
    if (!canvas || !emptyEl) {
        return;
    }

    const records = (history || []).filter(item => item && (item.dividend_per_share != null || item.dividend_yield != null));
    if (records.length === 0) {
        canvas.classList.add('hidden');
        emptyEl.classList.remove('hidden');
        return;
    }

    canvas.classList.remove('hidden');
    emptyEl.classList.add('hidden');

    const labels = records.map(item => item.date);
    const perShareSeries = records.map(item => item.dividend_per_share != null ? Number(item.dividend_per_share) : null);
    const yieldSeries = records.map(item => item.dividend_yield != null ? Number(item.dividend_yield) : null);

    let currencyPrefix = `${currency || ''} `;
    if (!currency || currency === 'USD') currencyPrefix = '$';
    if (currency === 'JPY') currencyPrefix = '¥';

    state.dividendChartInstance = new Chart(canvas.getContext('2d'), {
        data: {
            labels,
            datasets: [
                {
                    type: 'bar',
                    label: '年間1株配当',
                    data: perShareSeries,
                    yAxisID: 'yDividend',
                    backgroundColor: 'rgba(14, 165, 233, 0.45)',
                    borderColor: '#0ea5e9',
                    borderWidth: 1,
                    borderRadius: 6,
                    maxBarThickness: 34,
                    order: 2,
                },
                {
                    type: 'line',
                    label: '配当利回り（年利）',
                    data: yieldSeries,
                    yAxisID: 'yYield',
                    borderColor: '#f59e0b',
                    backgroundColor: 'rgba(245, 158, 11, 0.15)',
                    borderWidth: 2,
                    tension: 0.25,
                    pointRadius: 2,
                    pointHoverRadius: 5,
                    fill: false,
                    order: 1,
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: {
                    labels: {
                        color: '#94a3b8',
                        boxWidth: 14,
                        usePointStyle: true,
                    },
                },
                tooltip: {
                    backgroundColor: 'rgba(17, 24, 39, 0.95)',
                    titleColor: '#94a3b8',
                    bodyColor: '#f1f5f9',
                    borderColor: 'rgba(255,255,255,0.1)',
                    borderWidth: 1,
                    callbacks: {
                        label: ctx => {
                            const value = ctx.parsed.y;
                            if (value == null) return `  ${ctx.dataset.label}: —`;
                            if (ctx.dataset.yAxisID === 'yYield') {
                                return `  ${ctx.dataset.label}: ${Number(value).toFixed(2)}%`;
                            }
                            return `  ${ctx.dataset.label}: ${currencyPrefix}${Number(value).toFixed(4)}`;
                        },
                        afterBody: tooltipItems => {
                            const firstItem = tooltipItems && tooltipItems.length > 0 ? tooltipItems[0] : null;
                            if (!firstItem) return [];

                            const record = records[firstItem.dataIndex];
                            if (!record || record.year_end_close == null) {
                                return [' ', '利回り計算株価: —'];
                            }

                            return [
                                ' ',
                                `利回り計算株価(年末終値): ${currencyPrefix}${Number(record.year_end_close).toFixed(2)}`,
                            ];
                        },
                    },
                },
            },
            scales: {
                x: {
                    ticks: { color: '#64748b', font: { size: 11 }, maxRotation: 0, maxTicksLimit: 8 },
                    grid: { color: 'rgba(255,255,255,0.04)', drawBorder: false },
                    border: { display: false },
                },
                yDividend: {
                    type: 'linear',
                    position: 'left',
                    ticks: {
                        color: '#38bdf8',
                        font: { size: 11 },
                        callback: value => `${currencyPrefix}${Number(value).toFixed(2)}`,
                    },
                    grid: { color: 'rgba(255,255,255,0.04)', drawBorder: false },
                    border: { display: false },
                },
                yYield: {
                    type: 'linear',
                    position: 'right',
                    ticks: {
                        color: '#f59e0b',
                        font: { size: 11 },
                        callback: value => `${Number(value).toFixed(1)}%`,
                    },
                    grid: { drawOnChartArea: false, drawBorder: false },
                    border: { display: false },
                },
            },
        },
    });
}

Object.assign(window, {
    changeFinancialMetric,
});
