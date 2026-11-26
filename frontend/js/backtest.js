import { fetchAPI } from './api.js';
import { openAuthModal } from './auth.js';
import { bindSavedRuns, refreshSavedPanel } from './backtest-saved.js';
import { normalizeSymbol } from './router.js';
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
const TRADE_REASONS = { signal: 'シグナル', rebalance: 'リバランス' };

let strategies = [];
let registryRequest;
let busy = false;
let chart;
let trades = [];
let gridResult;
let latest;
let requestedRun;
let openedRun;
let openVersion = 0;

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
    byId('watchlist-symbol').innerHTML = option('', '候補を選択') + byId('symbols').innerHTML;
}

export function refreshBacktestSession() {
    updateBacktestSymbols();
    refreshSavedPanel(Boolean(state.currentUser?.access_token), setError);
    if (!state.currentUser?.access_token) {
        openVersion += 1;
        clearResult();
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
    if (requestedRun && state.currentUser?.access_token && openedRun !== requestedRun &&
        window.location?.hash.startsWith('#/backtest?run=')) openSavedRun(requestedRun);
    updateControls();
    return registryRequest;
}

function localDate(date) {
    return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

export function openBacktest(symbol, { run } = {}) {
    if (requestedRun !== run) {
        openVersion += 1;
        clearResult();
    }
    requestedRun = run;
    const prefilled = symbol || (!byId('symbol').value && state.currentSymbol);
    if (prefilled) byId('symbol').value = prefilled;
    if (!byId('end').value) {
        const now = new Date();
        byId('end').value = localDate(now);
        now.setDate(now.getDate() - 365);
        byId('start').value = localDate(now);
    }
    return refreshBacktestSession();
}

function clearResult() {
    for (const id of ['grid-result', 'result', 'advanced', 'result-actions']) byId(id).classList.add('hidden');
    byId('saved-label').textContent = '';
    openedRun = null;
    latest = null;
    gridResult = null;
    trades = [];
    chart?.destroy();
    chart = null;
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
    const kind = byId('kind').value || 'single';
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
    if (byId('eps-lag').value !== '') body.eps_lag_days = Number(byId('eps-lag').value);
    if (byId('risk-free').value !== '') body.risk_free = Number(byId('risk-free').value);
    if (kind === 'portfolio') {
        const raw = byId('portfolio-symbols').value.trim();
        const symbols = raw.split(/[,、\s]+/).map(normalizeSymbol);
        if (symbols.some(symbol => !symbol) || symbols.length < 2 || symbols.length > 10 ||
            new Set(symbols).size !== symbols.length) {
            throw new Error('正しい形式の重複のない銘柄を2〜10件入力してください。');
        }
        delete body.symbol;
        body.symbols = symbols;
        body.allocation = byId('allocation').value;
        body.rebalance_every = byId('rebalance').value === '' ? null : Number(byId('rebalance').value);
        body.rebalance_threshold_pct = Number(byId('rebalance-threshold').value || 5);
    } else if (kind === 'walkforward' || byId('mode').value === 'grid') {
        body.rank_by = byId('rank-by').value;
        body.grid = Object.fromEntries(params.flatMap((param, index) => {
            const raw = byId(`grid-param-${index}`).value.trim();
            return raw ? [[param.name, raw.split(',').map(value => readParam(param, value.trim()))]] : [];
        }));
        if (kind === 'walkforward') {
            body.folds = Number(byId('fold-count').value);
            body.window = byId('window').value;
        }
    }
    return body;
}

function populateBacktestForm(kind, request) {
    const fields = {
        kind: kind === 'grid' ? 'single' : kind,
        mode: kind === 'grid' ? 'grid' : 'run',
        symbol: request.symbol ?? '',
        'portfolio-symbols': request.symbols?.join(', ') ?? '',
        start: request.start_date, end: request.end_date, interval: request.interval ?? '1d',
        strategy: request.strategy ?? strategies[0]?.name ?? '',
        execution: request.execution ?? 'next_open', fee: request.fee_rate ?? 0, slippage: request.slippage_bps ?? 0,
        capital: request.initial_capital ?? 1000000, 'eps-lag': request.eps_lag_days ?? 90, 'risk-free': request.risk_free ?? 0,
        allocation: request.allocation ?? 'equal', rebalance: request.rebalance_every,
        'rebalance-threshold': request.rebalance_threshold_pct ?? 5,
        'fold-count': request.folds ?? 3, window: request.window ?? 'rolling', 'rank-by': request.rank_by ?? 'sharpe',
    };
    for (const [id, value] of Object.entries(fields)) byId(id).value = String(value ?? '');
    renderParams();
    for (const [index, param] of selectedParams().entries()) {
        const value = Object.hasOwn(request.params ?? {}, param.name) ? request.params[param.name] : param.default;
        byId(`param-${index}`).value = String(value ?? '');
        byId(`grid-param-${index}`).value = (request.grid?.[param.name] ?? []).map(value => String(value)).join(', ');
    }
    updateBacktestMode();
}

const displayMetric = (key, value) => Number.isFinite(value)
    ? `${value.toLocaleString('ja-JP', { maximumFractionDigits: 2 })}${PERCENT_METRICS.has(key) ? '%' : ''}`
    : '—';

function renderTable(headers, rows) {
    return `<table class="data-table"><thead><tr>${headers.map(label => `<th>${escapeHtml(label)}</th>`).join('')}</tr></thead>
        <tbody>${rows.join('')}</tbody></table>`;
}

export function renderRunResult(data, symbol, kind = 'single') {
    const walk = kind === 'walkforward';
    const strategyLabel = walk ? '検証期間（OOS）' : '戦略';
    byId('result-title').textContent = `${symbol.toUpperCase()} の${walk ? 'ウォークフォワード結果' : 'バックテスト結果'}`;
    byId('chart').setAttribute('aria-label', walk ? '連結した検証期間（OOS）の資産推移' : 'バックテストの資産推移');
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
    const columns = kind === 'portfolio' ? { ...TRADE_COLUMNS, reason: '理由' } : TRADE_COLUMNS;
    byId('trades').innerHTML = trades.length ? renderTable(Object.values(columns), trades.map(trade => {
        const cells = Object.keys(columns).map(key => {
            let value = trade[key] ?? '—';
            if (key === 'open_fallback' && typeof value === 'boolean') value = value ? 'はい' : 'いいえ';
            else if (key === 'side') value = { BUY: '買い', SELL: '売り' }[value] ?? value;
            else if (key === 'reason') value = TRADE_REASONS[value] ?? value;
            else if (typeof value === 'number') value = displayMetric(key, value);
            return `<td>${escapeHtml(value)}</td>`;
        });
        return `<tr>${cells.join('')}</tr>`;
    })) : '<p>取引はありません。</p>';

    byId('result').classList.remove('hidden');
    chart?.destroy();
    const final = data.equity_curve.at(-1);
    byId('equity-summary').textContent = final
        ? `${final.date} の資産　${strategyLabel} ${displayMetric('equity', final.equity)}、買い持ち ${displayMetric('equity', final.benchmark_equity)}`
        : '資産推移データがありません';
    chart = new Chart(byId('chart'), {
        type: 'line',
        data: {
            labels: data.equity_curve.map(row => row.date),
            datasets: [
                { label: strategyLabel, data: data.equity_curve.map(row => row.equity), borderColor: '#3b82f6', pointRadius: 0 },
                { label: '買い持ち', data: data.equity_curve.map(row => row.benchmark_equity), borderColor: '#10b981',
                    borderDash: [5, 3], pointRadius: 0 },
            ],
        },
        options: { responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: 'index', intersect: false } },
    });
}

const cells = values => `<tr>${values.map(value => `<td>${escapeHtml(value)}</td>`).join('')}</tr>`;
const parameterText = params => Object.entries(params).map(([key, value]) => `${key}=${value ?? '無効'}`).join(' / ');
const holdingText = value => Number.isFinite(value) ? value.toLocaleString('ja-JP', { maximumFractionDigits: 0 }) : '—';

export function renderAdvancedResult(data, kind) {
    const portfolio = kind === 'portfolio';
    const rankBy = data.robustness?.rank_by;
    const metric = RANK_LABELS[rankBy] ?? rankBy;
    byId('advanced').classList.remove('hidden');
    byId('advanced-title').textContent = portfolio ? '銘柄別の損益寄与' : '分割ごとの学習・検証結果';
    byId('robustness').textContent = portfolio ? '' :
        `利益が出た検証期間の割合 ${displayMetric('return_pct', 100 * data.robustness.profitable_share)}。 ` +
        `${metric}の平均悪化幅 ${displayMetric(rankBy, data.robustness.metric_degradation)}（${data.robustness.paired_folds}分割）。`;
    byId('advanced-table').innerHTML = portfolio
        ? renderTable(['銘柄', '損益寄与', '最終保有額'], Object.entries(data.contributions).map(([symbol, pnl]) =>
            cells([symbol, holdingText(pnl), holdingText(data.equity_curve.at(-1)?.holdings?.[symbol])])))
        : renderTable(['分割', '学習期間（IS）', '検証期間（OOS）', '選ばれたパラメータ', `IS ${metric}`,
            `OOS ${metric}`, 'OOS 累積リターン', 'OOS 最大ドローダウン', '注記'], data.folds.map(fold => cells([
            fold.fold, `${fold.train_start} ～ ${fold.train_end}`, `${fold.test_start} ～ ${fold.test_end}`,
            parameterText(fold.params), displayMetric(rankBy, fold.is_metric), displayMetric(rankBy, fold.oos_metrics[rankBy]),
            displayMetric('return_pct', fold.oos_metrics.return_pct), displayMetric('max_drawdown', fold.oos_metrics.max_drawdown),
            fold.warning ?? '—',
        ])));
}

export function renderGridResult(data) {
    const metrics = [...new Set([data.rank_by, 'return_pct', 'cagr', 'sharpe', 'max_drawdown'])];
    const headers = ['順位', 'パラメータ', ...metrics.map(key => RANK_LABELS[key] ?? key), '実行'];
    byId('grid-table').innerHTML = renderTable(headers, data.results.map((row, index) => {
        const params = parameterText(row.params);
        const cells = metrics.map(key => `<td>${displayMetric(key, row.metrics[key])}</td>`).join('');
        return `<tr><td>${escapeHtml(row.rank)}</td><td>${escapeHtml(params)}</td>${cells}
            <td><button class="btn-primary btn-sm" type="button" data-backtest-row="${index}">この設定で実行</button></td></tr>`;
    }));
    byId('grid-result').classList.remove('hidden');
}

export function tradesToCsv(rows) {
    const fields = Object.keys(TRADE_COLUMNS);
    const includeReason = rows.some(row => row.reason != null);
    const csvCell = value => {
        let text = String(value ?? '');
        // A tab inside the quoted field neutralises spreadsheet formulas.
        if (/^[\s\u0000-\u001f]*[=+@-]/.test(text)) text = `\t${text}`;
        return `"${text.replaceAll('"', '""')}"`;
    };
    const header = includeReason ? [...fields, '理由'] : fields;
    const records = rows.map(row => {
        const values = fields.map(key => row[key]);
        return includeReason ? [...values, TRADE_REASONS[row.reason] ?? row.reason] : values;
    });
    return [header, ...records].map(row => row.map(csvCell).join(',')).join('\r\n') + '\r\n';
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

function displayResult(kind, body, data) {
    clearResult();
    if (kind === 'grid') {
        const { grid, rank_by, ...base } = body;
        gridResult = { base, rows: data.results };
        renderGridResult(data);
    } else {
        renderRunResult(data, body.symbol ?? body.symbols.join(', '), kind);
        if (kind !== 'single') renderAdvancedResult(data, kind);
    }
    byId('result-actions').classList.remove('hidden');
}

async function openSavedRun(id) {
    const version = ++openVersion;
    const user = state.currentUser;
    setError();
    try {
        const row = await fetchAPI(`/backtest/runs/${encodeURIComponent(id)}`, { cache: 'no-store' });
        await registryRequest;
        if (version !== openVersion || user !== state.currentUser) return;
        populateBacktestForm(row.kind, row.request);
        displayResult(row.kind, row.request, row.result);
        openedRun = id;
        byId('save').disabled = true;
        showSavedLabel(row);
    } catch (error) {
        if (version === openVersion && user === state.currentUser) setError(error.message);
    }
}

function showSavedLabel(row) {
    byId('saved-label').textContent = `保存済み「${row.name}」。指標はクライアントから受信した値です。${row.note ? ` メモ：${row.note}` : ''}`;
}

async function runBacktest(body) {
    if (busy || !state.currentUser?.access_token) return;
    if (requestedRun && window.location?.hash.startsWith('#/backtest?run=')) {
        requestedRun = undefined;
        window.location.hash = '#/backtest';
    }
    const version = ++openVersion;
    const user = state.currentUser;
    busy = true;
    setError();
    updateControls();
    try {
        const endpoint = body.symbols ? 'portfolio' : body.folds !== undefined ? 'walkforward' : body.grid ? 'grid' : 'run';
        const kind = endpoint === 'run' ? 'single' : endpoint;
        const data = await fetchAPI(`/backtest/${endpoint}`, {
            method: 'POST', body: JSON.stringify(body),
        }, 'バックテストの実行に失敗しました');
        if (version !== openVersion || user !== state.currentUser) return;
        displayResult(kind, body, data);
        latest = { kind, request: body, result: data };
        byId('save').disabled = false;
    } catch (error) {
        if (version === openVersion && user === state.currentUser) setError(error.message);
    } finally {
        busy = false;
        updateControls();
    }
}

export function updateBacktestMode() {
    const kind = byId('kind').value || 'single';
    const portfolio = kind === 'portfolio';
    const walk = kind === 'walkforward';
    const grid = kind === 'single' && byId('mode').value === 'grid';
    for (const [id, hidden] of Object.entries({
        'symbol-field': portfolio, 'mode-field': portfolio || walk, 'portfolio-options': !portfolio,
        'walk-options': !walk, 'grid-options': !grid && !walk,
    })) byId(id).classList.toggle('hidden', hidden);
    byId('symbol').disabled = portfolio;
    byId('fold-count').disabled = !walk;
    byId('rebalance').disabled = !portfolio;
    byId('rebalance-threshold').disabled = !portfolio;
    byId('submit').textContent = walk ? 'ウォークフォワードを実行' : grid ? 'グリッド探索を実行' : 'バックテストを実行';
}

export function bindBacktestEvents() {
    byId('login-btn').addEventListener('click', event => openAuthModal('login', event.currentTarget));
    byId('strategy').addEventListener('change', renderParams);
    byId('mode').addEventListener('change', updateBacktestMode);
    byId('kind').addEventListener('change', updateBacktestMode);
    byId('add-symbol').addEventListener('click', () => {
        const selected = normalizeSymbol(byId('watchlist-symbol').value);
        const field = byId('portfolio-symbols');
        const symbols = field.value.trim().split(/[,、\s]+/).filter(Boolean);
        if (selected && symbols.length < 10 && !symbols.some(symbol => normalizeSymbol(symbol) === selected)) {
            field.value = [...symbols, selected].join(', ');
        }
        field.focus();
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
    bindSavedRuns({
        getLatest: () => latest, onError: setError,
        onSaved: (data, row) => {
            if (latest !== data) return;
            latest = null;
            byId('save').disabled = true;
            byId('saved-label').textContent = `「${row.name}」を保存しました。`;
        },
        onRename: row => {
            if (openedRun === row.id) showSavedLabel(row);
        },
        onDelete: id => {
            if (openedRun === id) {
                window.location.hash = '#/backtest';
                clearResult();
            }
        },
    });
    updateBacktestMode();
}
