/**
 * Stock Analyzer — Price chart and period controls
 */

import { state } from './state.js';
import { fetchAPI } from './api.js';

// ===== Indicators & Chart Helper =====
export function updateChartVisibility() {
    if (state.currentChartHistory.length > 0) {
        renderChart(state.currentChartHistory, state.currentChartCurrency);
    }
}

export function clearPriceAndRsiCharts() {
    if (state.priceChart) {
        state.priceChart.destroy();
        state.priceChart = null;
    }
    if (state.rsiChartInstance) {
        state.rsiChartInstance.destroy();
        state.rsiChartInstance = null;
    }

    const priceCanvas = document.getElementById('price-chart');
    if (priceCanvas) {
        const ctx = priceCanvas.getContext('2d');
        ctx.clearRect(0, 0, priceCanvas.width, priceCanvas.height);
    }

    const rsiCanvas = document.getElementById('rsi-chart');
    if (rsiCanvas) {
        const ctx = rsiCanvas.getContext('2d');
        ctx.clearRect(0, 0, rsiCanvas.width, rsiCanvas.height);
    }
}

export function setChartLoading(isLoading) {
    const overlay = document.getElementById('chart-loading-overlay');
    if (!overlay) return;
    overlay.classList.toggle('hidden', !isLoading);
}

// ===== Chart =====
export async function loadChart(symbol, period, currency) {
    const requestId = ++state.latestChartRequestId;
    setChartLoading(true);
    clearPriceAndRsiCharts();

    const periodMap = {
        '1mo': { months: 1 },
        '3mo': { months: 3 },
        '6mo': { months: 6 },
        '1y': { months: 12 },
        '2y': { months: 24 },
    };

    const p = periodMap[period] || periodMap['3mo'];
    const endDate = new Date();
    const startDate = new Date();

    // SMA 25/50/75/200を日次ベースで算出させるため、全期間で1dを使う
    const interval = '1d';
    if (period === '10y') {
        startDate.setFullYear(startDate.getFullYear() - 10);
    } else {
        startDate.setMonth(startDate.getMonth() - p.months);
    }

    const startStr = startDate.toISOString().split('T')[0];
    const endStr = endDate.toISOString().split('T')[0];

    try {
        const data = await fetchAPI(`/stock/${symbol}/history?start_date=${startStr}&end_date=${endStr}&interval=${interval}`);

        // 先に送信された遅いレスポンスで現在表示を上書きしない
        if (requestId !== state.latestChartRequestId || symbol !== state.currentSymbol) {
            return;
        }

        state.currentChartHistory = data.history;
        state.currentChartCurrency = currency;
        renderChart(data.history, currency);
    } catch (err) {
        if (requestId === state.latestChartRequestId && symbol === state.currentSymbol) {
            console.error('Chart load error:', err);
        }
    } finally {
        if (requestId === state.latestChartRequestId && symbol === state.currentSymbol) {
            setChartLoading(false);
        }
    }
}

export function renderChart(history, currency) {
    const ctx = document.getElementById('price-chart').getContext('2d');
    const rsiCtx = document.getElementById('rsi-chart').getContext('2d');
    clearPriceAndRsiCharts();

    let closes = history.map(h => h.close);

    // 円換算（USDの場合のみ）
    if ((!currency || currency === 'USD') && state.exchangeRateUSDJPY) {
        closes = closes.map(c => c * state.exchangeRateUSDJPY);
    }

    const labels = history.map(h => h.date);

    const showSma25 = document.getElementById('toggle-sma25').checked;
    const showSma50 = document.getElementById('toggle-sma50').checked;
    const showSma75 = document.getElementById('toggle-sma75').checked;
    const showSma200 = document.getElementById('toggle-sma200').checked;
    const showRSI = document.getElementById('toggle-rsi').checked;

    document.getElementById('rsi-container').classList.toggle('hidden', !showRSI);

    const datasets = [];

    const isUp = closes.length >= 2 && closes[closes.length - 1] >= closes[0];
    const lineColor = isUp ? '#10b981' : '#f43f5e';
    const fillColor = isUp ? 'rgba(16, 185, 129, 0.08)' : 'rgba(244, 63, 94, 0.08)';

    datasets.push({
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
        order: 1
    });

    const getIndicatorData = (key) => {
        let values = history.map(h => h[key]);
        if ((!currency || currency === 'USD') && state.exchangeRateUSDJPY) {
            // 値段に関する指標は為替レートを乗算
            values = values.map(v => v !== null && v !== undefined ? v * state.exchangeRateUSDJPY : null);
        }
        return values;
    };

    if (showSma25) datasets.push({ label: 'SMA 25', data: getIndicatorData('sma_25'), borderColor: '#3b82f6', borderWidth: 1.5, pointRadius: 0, tension: 0.3, fill: false, order: 2 });
    if (showSma50) datasets.push({ label: 'SMA 50', data: getIndicatorData('sma_50'), borderColor: '#f59e0b', borderWidth: 1.5, pointRadius: 0, tension: 0.3, fill: false, order: 3 });
    if (showSma75) datasets.push({ label: 'SMA 75', data: getIndicatorData('sma_75'), borderColor: '#8b5cf6', borderWidth: 1.5, pointRadius: 0, tension: 0.3, fill: false, order: 4 });
    if (showSma200) datasets.push({ label: 'SMA 200', data: getIndicatorData('sma_200'), borderColor: '#ec4899', borderWidth: 1.5, pointRadius: 0, tension: 0.3, fill: false, order: 5 });

    state.priceChart = new Chart(ctx, {
        type: 'line',
        data: { labels, datasets },
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
                    padding: 12,
                    cornerRadius: 8,
                    titleFont: { size: 12 },
                    bodyFont: { size: 13, weight: '500' },
                    callbacks: {
                        label: ctx => {
                            const val = ctx.parsed.y;
                            const dsLabel = ctx.dataset.label;
                            let formatted = val.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
                            if ((!currency || currency === 'USD') && state.exchangeRateUSDJPY) {
                                formatted = `¥${val.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
                            }
                            return `  ${dsLabel}: ${formatted}`;
                        }
                    }
                }
            },
            scales: {
                x: {
                    grid: { color: 'rgba(255,255,255,0.04)', drawBorder: false },
                    ticks: { color: '#64748b', font: { size: 11 }, maxTicksLimit: 8, maxRotation: 0 },
                    border: { display: false },
                },
                y: {
                    position: 'right',
                    grid: { color: 'rgba(255,255,255,0.04)', drawBorder: false },
                    ticks: { color: '#64748b', font: { size: 11 }, callback: v => v.toLocaleString() },
                    border: { display: false },
                }
            }
        }
    });

    if (showRSI) {
        const rsiData = history.map(h => h.rsi_14);
        state.rsiChartInstance = new Chart(rsiCtx, {
            type: 'line',
            data: {
                labels,
                datasets: [{
                    label: 'RSI (14)',
                    data: rsiData,
                    borderColor: '#a855f7',
                    borderWidth: 1.5,
                    pointRadius: 0,
                    pointHitRadius: 10,
                    pointHoverRadius: 5,
                    pointHoverBackgroundColor: '#a855f7',
                    tension: 0.3,
                    fill: {
                        target: { value: 30 },
                        below: 'rgba(168, 85, 247, 0.1)'
                    }
                }]
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
                        padding: 12,
                        cornerRadius: 8,
                        titleFont: { size: 12 },
                        bodyFont: { size: 13, weight: '500' },
                        callbacks: {
                            label: ctx => `  RSI: ${ctx.parsed.y.toFixed(1)}`
                        }
                    }
                },
                scales: {
                    x: {
                        grid: { display: false },
                        ticks: { display: false },
                        border: { display: false },
                    },
                    y: {
                        position: 'right',
                        min: 0,
                        max: 100,
                        grid: { color: 'rgba(255,255,255,0.04)', drawBorder: false },
                        ticks: { color: '#64748b', font: { size: 10 }, stepSize: 30 },
                        border: { display: false },
                    }
                }
            }
        });
    }
}

export function changePeriod(period) {
    state.currentPeriod = period;
    document.querySelectorAll('.period-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.period === period);
    });

    // 現在のprofileからcurrencyを取得する（レンダリング時に必要）
    // NOTE: quick hackとしてUIから取得するか、キャッシュから取得します
    const currentCurrency = document.getElementById('price-display').textContent.includes('¥') ? 'USD' : 'JPY';
    if (state.currentSymbol) loadChart(state.currentSymbol, period, currentCurrency);
}

export function bindChartEvents() {
    document.querySelectorAll('.period-btn[data-period]').forEach(btn => {
        btn.addEventListener('click', () => {
            const period = btn.dataset.period;
            if (period) changePeriod(period);
        });
    });

    ['toggle-sma25', 'toggle-sma50', 'toggle-sma75', 'toggle-sma200', 'toggle-rsi'].forEach(id => {
        document.getElementById(id)?.addEventListener('change', updateChartVisibility);
    });
}
