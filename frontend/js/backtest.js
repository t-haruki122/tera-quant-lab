import { fetchAPI } from './api.js';
import { openAuthModal } from './auth.js';
import { state } from './state.js';
import { escapeAttr, escapeHtml } from './utils.js';

const byId = id => document.getElementById(`backtest-${id}`);
const METRICS = {
    return_pct: '累積リターン', cagr: '年率リターン', sharpe: 'シャープレシオ', sortino: 'ソルティノレシオ',
    max_drawdown: '最大ドローダウン', win_rate: '勝率', total_trades: '取引回数（決済）', exposure: '保有期間の割合',
};
const RANK_LABELS = {
    ...METRICS, annualised_volatility: '年率ボラティリティ', initial_capital: '初期資金', final_asset: '最終資産',
    profit_factor: 'プロフィットファクター', average_win: '平均利益', average_loss: '平均損失',
    max_drawdown_duration: '最大ドローダウン期間', open_fallbacks: '終値での代替約定数',
    missing_eps_bars: 'EPS欠損バー数', benchmark_return_pct: '買い持ちの累積リターン',
    benchmark_cagr: '買い持ちの年率リターン', benchmark_max_drawdown: '買い持ちの最大ドローダウン',
    benchmark_open_fallbacks: '買い持ちの終値代替約定数',
};
const PERCENT_METRICS = new Set([
    'return_pct', 'cagr', 'max_drawdown', 'win_rate', 'exposure', 'annualised_volatility',
    'benchmark_return_pct', 'benchmark_cagr', 'benchmark_max_drawdown',
]);
const TRADE_COLUMNS = {
    symbol: '銘柄', date: '約定日', signal_date: 'シグナル日', side: '売買', price: '約定価格',
    shares: '株数', fee: '手数料', slippage: 'スリッページ', open_fallback: '終値で代替', pnl: '損益', cash_after: '残高',
};

let strategies = [];
let registryRequest;
let busy = false;
let chart;
let trades = [];
let gridResult;

function option(value, label = value, selected = false) {
    return `<option value="${escapeAttr(value)}"${selected ? ' selected' : ''}>${escapeHtml(label)}</option>`;
}

export function renderStrategies(data) {
    strategies = data.strategies;
    byId('strategy').innerHTML = strategies.map(({ name }) => option(name)).join('');
    byId('rank-by').innerHTML = data.rank_metrics.map(key => option(key, RANK_LABELS[key] ?? key, key === 'sharpe')).join('');
    renderParams();
}

function selectedParams() {
    const select = byId('strategy');
    return (strategies.find(item => item.name === select.value) ?? strategies[0])?.params ?? [];
}

export function renderParams() {
    const params = selectedParams();
    byId('params').innerHTML = params.map((param, index) => {
        const id = `backtest-param-${index}`;
        const choices = param.choices ?? (param.type === 'bool' ? [true, false] : null);
        const type = param.type === 'int' || param.type === 'float' ? 'number' : 'text';
        const min = param.min != null ? ` min="${escapeAttr(param.min)}"` : '';
        const max = param.max != null ? ` max="${escapeAttr(param.max)}"` : '';
        const step = type === 'number' ? (param.type === 'int' ? ' step="1"' : ' step="any"') : '';
        const control = choices
            ? `<select id="${id}">${choices.map(value => option(value, value, value === param.default)).join('')}</select>`
            : `<input id="${id}" type="${type}" value="${escapeAttr(param.default ?? '')}"${min}${max}${step}>`;
        return `<label class="form-group">${escapeHtml(param.name)}${control}<small>${escapeHtml(param.description)}</small></label>`;
    }).join('');
    byId('grid-params').innerHTML = params.map((param, index) =>
        `<label class="form-group">${escapeHtml(param.name)}<input id="backtest-grid-param-${index}" type="text"></label>`
    ).join('');
}

function setError(message = '') {
    byId('error').textContent = message;
    byId('error').classList.toggle('hidden', !message);
}

function updateControls() {
    const signedIn = Boolean(state.currentUser?.access_token);
    const status = busy ? 'バックテストを実行中…' : registryRequest ? '戦略を読み込み中…' : '';
    byId('login').classList.toggle('hidden', signedIn);
    byId('form').classList.toggle('hidden', !signedIn || !strategies.length);
    byId('fields').disabled = busy;
    byId('grid-table').querySelectorAll('button').forEach(button => { button.disabled = busy; });
    byId('status').textContent = signedIn ? status : '';
    byId('status').classList.toggle('hidden', !signedIn || !status);
}

export function updateBacktestSymbols() {
    byId('symbols').innerHTML = state.currentUser?.access_token
        ? state.listItems.map(({ symbol }) => option(symbol)).join('')
        : '';
}

export function refreshBacktestSession() {
    updateBacktestSymbols();
    if (!state.currentUser?.access_token) {
        byId('grid-result').classList.add('hidden');
        byId('result').classList.add('hidden');
        gridResult = null;
        trades = [];
        chart?.destroy();
        chart = null;
    } else if (!strategies.length && !registryRequest) {
        setError();
        registryRequest = fetchAPI('/backtest/strategies')
            .then(renderStrategies)
            .catch(error => setError(error.message))
            .finally(() => {
                registryRequest = null;
                updateControls();
            });
    }
    updateControls();
    return registryRequest;
}

function localDate(date) {
    return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

export function openBacktest(symbol) {
    const prefilled = symbol || state.currentSymbol;
    if (prefilled) byId('symbol').value = prefilled;
    if (!byId('end').value) {
        const now = new Date();
        byId('end').value = localDate(now);
        now.setDate(now.getDate() - 365);
        byId('start').value = localDate(now);
    }
    return refreshBacktestSession();
}

function readParam(param, value) {
    if (param.default === null && (value === '' || value === 'null')) return null;
    if (param.type === 'int' || param.type === 'float') {
        const number = Number(value);
        if (!value || !Number.isFinite(number) || (param.type === 'int' && !Number.isInteger(number))) {
            throw new Error(`${param.name} に有効な数値を入力してください。`);
        }
        return number;
    }
    if (param.type === 'bool') {
        if (value !== 'true' && value !== 'false') throw new Error(`${param.name} は true または false を入力してください。`);
        return value === 'true';
    }
    return value;
}

export function readBacktestRequest() {
    const params = selectedParams();
    const body = {
        symbol: byId('symbol').value.trim(),
        start_date: byId('start').value,
        end_date: byId('end').value,
        interval: byId('interval').value,
        strategy: byId('strategy').value,
        params: Object.fromEntries(params.map((param, index) =>
            [param.name, readParam(param, byId(`param-${index}`).value.trim())])),
        execution: byId('execution').value,
        fee_rate: Number(byId('fee').value),
        slippage_bps: Number(byId('slippage').value),
        initial_capital: Number(byId('capital').value),
    };
    if (byId('mode').value === 'grid') {
        body.rank_by = byId('rank-by').value;
        body.grid = Object.fromEntries(params.flatMap((param, index) => {
            const raw = byId(`grid-param-${index}`).value.trim();
            return raw ? [[param.name, raw.split(',').map(value => readParam(param, value.trim()))]] : [];
        }));
    }
    return body;
}

const displayMetric = (key, value) => Number.isFinite(value)
    ? `${value.toLocaleString('ja-JP', { maximumFractionDigits: 2 })}${PERCENT_METRICS.has(key) ? '%' : ''}`
    : '—';

function renderTable(headers, rows) {
    return `<table class="data-table"><thead><tr>${headers.map(label => `<th>${escapeHtml(label)}</th>`).join('')}</tr></thead>
        <tbody>${rows.join('')}</tbody></table>`;
}

export function renderRunResult(data, symbol) {
    byId('result-title').textContent = `${symbol.toUpperCase()} のバックテスト結果`;
    byId('metrics').innerHTML = Object.entries(METRICS).map(([key, label]) => {
        const value = data.benchmark_metrics[key];
        const benchmark = key === 'win_rate' && !data.benchmark_metrics.total_trades ? null : value;
        return `<div class="metric-card"><div class="metric-label">${label}</div>
            <div class="metric-value">${displayMetric(key, data.metrics[key])}</div>
            <div class="metric-unit">買い持ち ${displayMetric(key, benchmark)}</div></div>`;
    }).join('');
    const warnings = data.warnings ?? [];
    byId('warnings').innerHTML = warnings.map(warning => `<li>${escapeHtml(warning)}</li>`).join('');
    byId('warnings').classList.toggle('hidden', !warnings.length);

    trades = data.trades;
    byId('csv').disabled = !trades.length;
    byId('trades').innerHTML = trades.length ? renderTable(Object.values(TRADE_COLUMNS), trades.map(trade => {
        const cells = Object.keys(TRADE_COLUMNS).map(key => {
            let value = trade[key] ?? '—';
            if (key === 'open_fallback' && typeof value === 'boolean') value = value ? 'はい' : 'いいえ';
            else if (key === 'side') value = { BUY: '買い', SELL: '売り' }[value] ?? value;
            else if (typeof value === 'number') value = displayMetric(key, value);
            return `<td>${escapeHtml(value)}</td>`;
        });
        return `<tr>${cells.join('')}</tr>`;
    })) : '<p>取引はありません。</p>';

    byId('result').classList.remove('hidden');
    chart?.destroy();
    chart = new Chart(byId('chart'), {
        type: 'line',
        data: {
            labels: data.equity_curve.map(row => row.date),
            datasets: [
                { label: '戦略', data: data.equity_curve.map(row => row.equity), borderColor: '#3b82f6', pointRadius: 0 },
                { label: '買い持ち', data: data.equity_curve.map(row => row.benchmark_equity), borderColor: '#10b981', pointRadius: 0 },
            ],
        },
        options: { responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: 'index', intersect: false } },
    });
}

export function renderGridResult(data) {
    const metrics = [...new Set([data.rank_by, 'return_pct', 'cagr', 'sharpe', 'max_drawdown'])];
    const headers = ['順位', 'パラメータ', ...metrics.map(key => RANK_LABELS[key] ?? key), '実行'];
    byId('grid-table').innerHTML = renderTable(headers, data.results.map((row, index) => {
        const params = Object.entries(row.params).map(([key, value]) => `${key}=${value ?? '無効'}`).join(' / ');
        const cells = metrics.map(key => `<td>${displayMetric(key, row.metrics[key])}</td>`).join('');
        return `<tr><td>${escapeHtml(row.rank)}</td><td>${escapeHtml(params)}</td>${cells}
            <td><button class="btn-primary btn-sm" type="button" data-backtest-row="${index}">この設定で実行</button></td></tr>`;
    }));
    byId('grid-result').classList.remove('hidden');
}

export function tradesToCsv(rows) {
    const fields = Object.keys(TRADE_COLUMNS);
    const csvCell = value => {
        let text = String(value ?? '');
        // A tab inside the quoted field neutralises spreadsheet formulas.
        if (/^[\s\u0000-\u001f]*[=+@-]/.test(text)) text = `\t${text}`;
        return `"${text.replaceAll('"', '""')}"`;
    };
    return [fields, ...rows.map(row => fields.map(key => row[key]))].map(row => row.map(csvCell).join(',')).join('\r\n') + '\r\n';
}

function downloadTrades() {
    const blob = new Blob(['\uFEFF', tradesToCsv(trades)], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'backtest-trades.csv';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
}

async function runBacktest(body) {
    if (busy || !state.currentUser?.access_token) return;
    busy = true;
    setError();
    updateControls();
    try {
        const isGrid = body.grid !== undefined;
        const data = await fetchAPI(isGrid ? '/backtest/grid' : '/backtest/run', {
            method: 'POST', body: JSON.stringify(body),
        }, 'バックテストの実行に失敗しました');
        if (isGrid) {
            const { grid, rank_by, ...base } = body;
            gridResult = { base, rows: data.results };
            renderGridResult(data);
        } else {
            renderRunResult(data, body.symbol);
        }
    } catch (error) {
        setError(error.message);
    } finally {
        busy = false;
        updateControls();
    }
}

export function bindBacktestEvents() {
    byId('login-btn').addEventListener('click', () => openAuthModal('login'));
    byId('strategy').addEventListener('change', renderParams);
    byId('mode').addEventListener('change', event => {
        const grid = event.target.value === 'grid';
        byId('grid-options').classList.toggle('hidden', !grid);
        byId('submit').textContent = grid ? 'グリッド探索を実行' : 'バックテストを実行';
    });
    byId('form').addEventListener('submit', event => {
        event.preventDefault();
        try {
            runBacktest(readBacktestRequest());
        } catch (error) {
            setError(error.message);
        }
    });
    byId('grid-table').addEventListener('click', event => {
        const button = event.target.closest('[data-backtest-row]');
        const row = gridResult?.rows[button?.dataset.backtestRow];
        if (row) runBacktest({ ...gridResult.base, params: row.params });
    });
    byId('csv').addEventListener('click', downloadTrades);
}
