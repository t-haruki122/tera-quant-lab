import { state } from './state.js';
import { fetchHistory } from './history.js';
import { formatMarketValue, formatYieldChange } from './market-format.js';
import { formatCurrency, formatPercent, staleMessage } from './utils.js';

const CHART_TYPE_KEY = 'stockAnalyzerChartType';
const SMA_COLORS = { 25: '#60a5fa', 50: '#fbbf24', 75: '#a78bfa', 200: '#f472b6' };
const toggles = ['sma25', 'sma50', 'sma75', 'sma200', 'rsi', 'volume', 'bollinger', 'macd'];
const instances = ['priceChart', 'rsiChartInstance', 'volumeChartInstance', 'macdChartInstance'];
const byId = id => document.getElementById(id);

export function readChartType() {
    try {
        return localStorage.getItem(CHART_TYPE_KEY) === 'candlestick' ? 'candlestick' : 'line';
    } catch {
        return 'line';
    }
}

let chartType = 'line';
let activeHistoryRequest = null;

function chartInterval(period) {
    return chartType === 'candlestick' && period === '10y' ? '1wk' : '1d';
}

function destroyCharts() {
    for (const key of instances) {
        state[key]?.destroy();
        state[key] = null;
    }
}

export function resetPriceChart() {
    ++state.latestChartRequestId;
    activeHistoryRequest = null;
    state.currentChartHistory = [];
    state.currentChartCurrency = null;
    destroyCharts();
    byId('price-chart-summary').textContent = '';
    for (const key of ['rsi', 'volume', 'macd']) byId(`${key}-container`).classList.add('hidden');
}

export function updateChartVisibility() {
    if (state.currentChartHistory.length) {
        renderChart(state.currentChartHistory, state.currentChartCurrency, activeHistoryRequest?.unit);
    }
}

export function updateChartCurrency(currency, unit = activeHistoryRequest?.unit) {
    if (currency === state.currentChartCurrency && unit === activeHistoryRequest?.unit) return;
    state.currentChartCurrency = currency;
    if (activeHistoryRequest) Object.assign(activeHistoryRequest, { currency, unit });
    updateChartVisibility();
}

export async function loadChart(symbol, period, currency, unit) {
    resetPriceChart();
    const interval = chartInterval(period);
    const requestId = state.latestChartRequestId;
    activeHistoryRequest = { symbol, period, currency, unit, interval };
    state.currentChartCurrency = currency;
    byId('history-stale').textContent = '';
    byId('chart-history-status').textContent = interval === '1wk' ? '週足で表示' : '';
    byId('chart-loading-overlay').classList.remove('hidden');

    const isCurrent = () => requestId === state.latestChartRequestId && symbol === state.currentSymbol;
    try {
        const data = await fetchHistory(symbol, period, interval);
        if (!isCurrent()) return;
        if (!data.history.length) {
            byId('chart-history-status').textContent = '価格履歴がありません';
            return;
        }
        state.currentChartHistory = data.history;
        activeHistoryRequest.unit = data.unit ?? activeHistoryRequest.unit;
        byId('history-stale').textContent = staleMessage(data);
        updateChartVisibility();
    } catch (error) {
        if (isCurrent()) byId('chart-history-status').textContent = error.message;
    } finally {
        if (isCurrent()) byId('chart-loading-overlay').classList.add('hidden');
    }
}

function line(label, data, borderColor, options = {}) {
    return { type: 'line', label, data, borderColor, borderWidth: 1.5, pointRadius: 0, ...options };
}

export function candlestickDatasets(history, scale, colors) {
    const pairs = (first, second) => history.map(bar =>
        Number.isFinite(bar[first]) && Number.isFinite(bar[second])
            ? [bar[first] * scale, bar[second] * scale] : null);
    const directions = history.map(bar => bar.close >= bar.open ? colors.up : colors.down);
    const fill = history.map((bar, i) => bar.close >= bar.open ? colors.background : directions[i]);
    const bar = { type: 'bar', grouped: false, borderSkipped: false, skipNull: true };
    return [
        { ...bar, label: '高値・安値', data: pairs('low', 'high'), backgroundColor: directions,
            barThickness: 1, order: 2, wick: true },
        { ...bar, label: 'ローソク足', data: pairs('open', 'close'), backgroundColor: fill,
            borderColor: directions, borderWidth: 1, maxBarThickness: 14, minBarLength: 2, order: 1, candle: true },
    ];
}

export function buildChartDatasets(history, { type, enabled, scale, colors }) {
    const data = (key, factor = 1) => history.map(row => Number.isFinite(row[key]) ? row[key] * factor : null);
    const closes = data('close', scale);
    const rising = closes.at(-1) >= closes[0];
    const price = type === 'candlestick' ? candlestickDatasets(history, scale, colors) : [
        line('終値', closes, rising ? colors.up : colors.down, {
            borderWidth: 2, fill: 'origin',
            backgroundColor: rising ? 'rgba(16,185,129,0.08)' : 'rgba(244,63,94,0.08)', order: 1,
        }),
    ];
    for (const [period, color] of Object.entries(SMA_COLORS)) {
        if (enabled.has(`sma${period}`)) price.push(line(`SMA ${period}`, data(`sma_${period}`, scale), color, { order: 0 }));
    }
    if (enabled.has('bollinger')) {
        const color = '#67e8f9';
        price.push(
            line('BB 下限', data('bb_lower', scale), color, { order: 3 }),
            line('BB 上限', data('bb_upper', scale), color, {
                order: 3, fill: '-1', backgroundColor: 'rgba(103,232,249,0.12)',
            }),
            line('BB 中心', data('bb_middle', scale), color, { order: 0, borderDash: [5, 4] }),
        );
    }

    const rsi = enabled.has('rsi') ? [line('RSI (14)', data('rsi_14'), '#c084fc', {
        fill: { target: { value: 30 }, below: 'rgba(168,85,247,0.1)', above: 'transparent' },
    })] : [];
    const volume = enabled.has('volume') ? [
        { type: 'bar', label: '出来高', data: data('volume'), backgroundColor: '#647fde', maxBarThickness: 14 },
    ] : [];
    const macd = enabled.has('macd') ? [
        line('MACD', data('macd', scale), '#60a5fa', { order: 0 }),
        line('シグナル', data('macd_signal', scale), '#fbbf24', { order: 0, borderDash: [5, 3] }),
        { type: 'bar', label: 'ヒストグラム', data: data('macd_hist', scale), backgroundColor: '#8275ce', order: 1,
            maxBarThickness: 14 },
    ] : [];
    return { price, rsi, volume, macd };
}

function palette() {
    const css = globalThis.getComputedStyle?.(document.documentElement);
    return {
        up: css?.getPropertyValue('--accent-emerald').trim() || '#10b981',
        down: css?.getPropertyValue('--accent-rose').trim() || '#f43f5e',
        background: css?.getPropertyValue('--bg-secondary').trim() || '#111827',
    };
}

function number(value) {
    return value.toLocaleString('ja-JP', { maximumFractionDigits: 4 });
}

function chartOptions({ xLabels = true, offset = false, y = {}, tooltip = {}, legend = false, tickLabel = number } = {}) {
    const grid = { color: 'rgba(255,255,255,0.06)' };
    return {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        normalized: true,
        interaction: { mode: 'index', intersect: false },
        plugins: {
            legend: { display: legend, labels: { color: '#cbd5e1' } },
            tooltip,
        },
        scales: {
            x: {
                type: 'category', offset, grid: { display: false },
                ticks: { display: xLabels, color: '#94a3b8', maxTicksLimit: 8, maxRotation: 0, sampleSize: 16 },
                border: { display: false },
            },
            y: {
                position: 'right', beginAtZero: false, grid, ticks: { color: '#94a3b8', maxTicksLimit: 5, callback: tickLabel },
                border: { display: false }, ...y,
            },
        },
    };
}

function chartPrice(value, currency) {
    return currency ? formatCurrency(value, currency) : value?.toLocaleString('ja-JP') ?? '—';
}

function ohlcTooltip({ open, high, low, close }, displayPrice) {
    const direction = close === open ? '＝' : close > open ? '▲' : '▼';
    return [`${direction} 始値 ${displayPrice(open)}`, `高値 ${displayPrice(high)}`,
        `安値 ${displayPrice(low)}`, `終値 ${displayPrice(close)}`];
}

export function renderChart(history, currency, unit) {
    destroyCharts();
    if (!history.length) return;
    const scale = !unit && currency === 'USD' && state.exchangeRateUSDJPY || 1;
    const displayPrice = value => unit ? formatMarketValue(value, unit) : chartPrice(value, currency);
    const labels = history.map(bar => bar.date);
    const enabled = new Set(toggles.filter(key => byId(`toggle-${key}`).checked));
    const datasets = buildChartDatasets(history, { type: chartType, enabled, scale, colors: palette() });
    const first = history[0];
    const last = history.at(-1);
    const movement = unit === 'yield_pct' ? `期間変化 ${formatYieldChange(last.close - first.close)}`
        : `期間騰落率 ${first.close ? formatPercent((last.close / first.close - 1) * 100) : '—'}`;
    byId('price-chart-summary').textContent = `${last.date} 終値 ${displayPrice(last.close)}、${movement}`;

    const candles = chartType === 'candlestick';
    const tooltip = {
        filter: item => !item.dataset.wick || !Number.isFinite(history[item.dataIndex].open),
        callbacks: {
            label: item => item.dataset.candle || item.dataset.wick ? ohlcTooltip(history[item.dataIndex], displayPrice)
                : `${item.dataset.label} ${displayPrice(item.parsed.y / scale)}`,
        },
    };
    state.priceChart = new Chart(byId('price-chart').getContext('2d'), {
        type: 'line', data: { labels, datasets: datasets.price },
        options: chartOptions({ offset: candles, tooltip, tickLabel: unit === 'yield_pct' ? displayPrice : number }),
    });

    for (const key of ['rsi', 'volume', 'macd']) {
        const series = datasets[key];
        byId(`${key}-container`).classList.toggle('hidden', !series.length);
        if (!series.length) continue;
        byId(`${key}-chart-summary`).textContent = `${last.date} ` + series
            .map(dataset => `${dataset.label} ${dataset.data.at(-1) == null ? '—' : number(dataset.data.at(-1))}`).join('、');
        state[`${key}ChartInstance`] = new Chart(byId(`${key}-chart`).getContext('2d'), {
            type: 'line', data: { labels, datasets: series },
            options: chartOptions({
                xLabels: false, offset: candles, legend: key === 'macd',
                y: key === 'rsi' ? { min: 0, max: 100 } : { beginAtZero: true },
            }),
        });
    }
}

export function updatePeriodControls(period) {
    document.querySelectorAll('.period-btn[data-period]').forEach(button => {
        const selected = button.dataset.period === period;
        button.classList.toggle('active', selected);
        button.setAttribute('aria-pressed', String(selected));
    });
}

function updateChartTypeControls() {
    document.querySelectorAll('[data-chart-type]').forEach(button => {
        const selected = button.dataset.chartType === chartType;
        button.classList.toggle('active', selected);
        button.setAttribute('aria-pressed', String(selected));
    });
}

export function changeChartType(type) {
    if (!['line', 'candlestick'].includes(type) || type === chartType) return;
    chartType = type;
    try { localStorage.setItem(CHART_TYPE_KEY, type); } catch { /* ストレージ制限時も切り替える */ }
    updateChartTypeControls();
    if (activeHistoryRequest && activeHistoryRequest.interval !== chartInterval(activeHistoryRequest.period)) {
        const { symbol, period, currency, unit } = activeHistoryRequest;
        loadChart(symbol, period, currency, unit);
    } else {
        updateChartVisibility();
    }
}

export function bindChartEvents() {
    chartType = readChartType();
    updateChartTypeControls();
    document.querySelectorAll('[data-chart-type]').forEach(button => {
        button.addEventListener('click', () => changeChartType(button.dataset.chartType));
    });
    for (const key of toggles) byId(`toggle-${key}`)?.addEventListener('change', updateChartVisibility);
}
