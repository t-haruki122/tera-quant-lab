import { fetchHistory } from './history.js';
import { normalizeSymbol } from './router.js';
import { state } from './state.js';
import { escapeAttr, escapeHtml, formatPercent, staleMessage } from './utils.js';

const MAX_EXTRAS = 4;
const colors = ['#60a5fa', '#fbbf24', '#5eead4', '#f472b6', '#c4b5fd'];
const dashes = [[], [6, 3], [3, 3], [8, 3, 2, 3], [2, 4]];
const byId = id => document.getElementById(`compare-${id}`);
const extras = [];
let context = null;
let renderId = 0;
let chart = null;

function validCloses(history) {
    return new Map(history.filter(row => Number.isFinite(row.close)).map(row => [row.date, row.close]));
}

export function alignComparison(series) {
    const prepared = series.map(({ symbol, history }) => ({ symbol, closes: validCloses(history) }));
    if (!prepared.length) return { dates: [], series: [] };
    const common = [...prepared[0].closes.keys()].sort().filter(date => prepared.every(({ closes }) => closes.has(date)));
    const start = common.findIndex(date => prepared.every(({ closes }) => closes.get(date) !== 0));
    const dates = start === -1 ? [] : common.slice(start);
    return {
        dates,
        series: prepared.map(({ symbol, closes }) => ({
            symbol,
            values: dates.map(date => (closes.get(date) / closes.get(dates[0]) - 1) * 100),
        })),
    };
}

export async function collectComparison(symbols, request) {
    const results = await Promise.allSettled(symbols.map(async symbol => {
        const data = await request(symbol);
        if (!data.history.some(row => Number.isFinite(row.close) && row.close !== 0)) {
            throw new Error('比較できる株価履歴がありません');
        }
        return { symbol, history: data.history, notice: staleMessage(data) };
    }));
    return {
        series: results.filter(result => result.status === 'fulfilled').map(result => result.value),
        errors: results.flatMap((result, index) => result.status === 'rejected'
            ? [{ symbol: symbols[index], message: result.reason?.message || '取得に失敗しました' }] : []),
    };
}

export function comparisonMessages(items) {
    return items.map(({ symbol, message }) => `<li>${escapeHtml(symbol)}　${escapeHtml(message)}</li>`).join('');
}

export function resetComparison() {
    ++renderId;
    chart?.destroy();
    chart = null;
    byId('plot').classList.add('hidden');
    byId('summary').textContent = '';
}

export async function renderComparison(symbol, period) {
    resetComparison();
    const id = renderId;
    if (context?.symbol !== symbol || context?.period !== period || context?.user !== state.currentUser) {
        const duplicate = extras.indexOf(symbol);
        if (duplicate !== -1) extras.splice(duplicate, 1);
        context = { symbol, period, user: state.currentUser, requests: new Map() };
    }
    const { requests } = context;
    byId('series').innerHTML = extras.map(extra => `<li><span>${escapeHtml(extra)}</span>
        <button type="button" class="account-action" data-compare-remove="${escapeAttr(extra)}"
            aria-label="${escapeAttr(extra)} を比較から削除">削除</button></li>`).join('');
    byId('errors').innerHTML = '';
    byId('notices').innerHTML = '';
    byId('status').textContent = '';
    if (!extras.length) return;

    byId('status').textContent = '比較データを読み込み中…';
    const result = await collectComparison([symbol, ...extras], ticker => {
        if (!requests.has(ticker)) requests.set(ticker, fetchHistory(ticker, period));
        return requests.get(ticker);
    });
    if (id !== renderId) return;
    byId('errors').innerHTML = comparisonMessages(result.errors);
    byId('notices').innerHTML = comparisonMessages(result.series
        .filter(item => item.notice).map(item => ({ symbol: item.symbol, message: item.notice })));
    const { dates, series } = alignComparison(result.series);
    if (!dates.length) {
        byId('status').textContent = result.series.length
            ? '比較の基準にできる共通の取引日がありません' : '比較データを表示できません';
        return;
    }

    byId('status').textContent = '';
    byId('summary').textContent = `${dates[0]} から ${dates.at(-1)} まで　`
        + series.map(({ symbol: ticker, values }) => `${ticker} ${formatPercent(values.at(-1))}`).join('、');
    byId('plot').classList.remove('hidden');
    chart = new Chart(byId('chart'), {
        type: 'line',
        data: {
            labels: dates,
            datasets: series.map(({ symbol: ticker, values }, index) => ({
                label: ticker, data: values, borderColor: colors[index], borderDash: dashes[index],
                pointRadius: dates.length === 1 ? 3 : 0, borderWidth: 2,
            })),
        },
        options: {
            responsive: true, maintainAspectRatio: false, animation: false, normalized: true,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { labels: { color: '#cbd5e1' }, onClick: null },
                tooltip: { callbacks: { label: item => `${item.dataset.label} ${formatPercent(item.parsed.y)}` } },
            },
            scales: {
                x: { ticks: { color: '#94a3b8', maxTicksLimit: 8, maxRotation: 0, sampleSize: 16 } },
                y: { ticks: { color: '#94a3b8', callback: formatPercent }, suggestedMin: 0, suggestedMax: 0 },
            },
        },
    });
}

function updateSuggestions() {
    const symbols = [...new Set(state.listItems.map(item => normalizeSymbol(item.symbol)).filter(Boolean))];
    byId('symbols').innerHTML = symbols.map(symbol => `<option value="${escapeAttr(symbol)}"></option>`).join('');
}

export function bindComparisonEvents() {
    const input = byId('symbol');
    const error = byId('input-error');
    byId('form')?.addEventListener('submit', event => {
        event.preventDefault();
        const symbol = normalizeSymbol(input.value);
        if (!symbol) error.textContent = 'ティッカーは英数字と ^ . = - を使って20文字以内で入力してください';
        else if (symbol === state.currentSymbol || extras.includes(symbol)) error.textContent = 'すでに比較に含まれています';
        else if (extras.length >= MAX_EXTRAS) error.textContent = '追加できる比較銘柄は4件までです';
        else {
            error.textContent = '';
            input.value = '';
            extras.push(symbol);
            renderComparison(state.currentSymbol, state.currentPeriod);
        }
    });
    byId('series')?.addEventListener('click', event => {
        const button = event.target.closest('[data-compare-remove]');
        const index = extras.indexOf(button?.dataset.compareRemove);
        if (index === -1) return;
        extras.splice(index, 1);
        context?.requests.delete(button.dataset.compareRemove);
        error.textContent = '';
        renderComparison(state.currentSymbol, state.currentPeriod);
        input.focus();
    });
    document.addEventListener('watchlistchange', updateSuggestions);
    updateSuggestions();
}
