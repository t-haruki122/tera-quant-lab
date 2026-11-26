import { fetchAPI } from './api.js';
import { createDialog } from './dialog.js';
import { serializeRoute } from './router.js';
import { state } from './state.js';
import { escapeAttr, escapeHtml } from './utils.js';

const el = id => document.getElementById(`backtest-${id}`);
const root = '/backtest/runs';
const dialog = createDialog('backtest-run-dialog');
const KINDS = { single: '単一銘柄', grid: 'グリッド探索', portfolio: 'ポートフォリオ', walkforward: 'ウォークフォワード' };
const METRICS = {
    return_pct: '累積リターン', cagr: '年率リターン', max_drawdown: '最大ドローダウン',
    sharpe: 'シャープレシオ', total_trades: '取引回数（決済）', benchmark_return_pct: '買い持ちリターン',
};
const RANKED_METRICS = new Set(['return_pct', 'cagr', 'max_drawdown', 'sharpe']);
const COLORS = ['#3b82f6', '#10b981', '#e8790c', '#a855f7', '#e45069'];
const valueText = (key, value) => Number.isFinite(value)
    ? value.toLocaleString('ja-JP', { maximumFractionDigits: 2 }) + (['sharpe', 'total_trades'].includes(key) ? '' : '%')
    : '—';
const heading = labels => `<thead><tr>${labels.map(label => `<th scope="col">${escapeHtml(label)}</th>`).join('')}</tr></thead>`;
let records = [];
let selected = new Set();
let comparison;
let listVersion = 0;
let compareVersion = 0;
let action;

export function renderSavedRuns(runs, selection = new Set()) {
    if (!runs.length) return '<p>保存済みの結果はありません。</p>';
    const rows = runs.map(run => {
        const id = escapeAttr(run.id);
        const link = escapeAttr(serializeRoute({ view: 'backtest', run: run.id }));
        return `<tr>
            <td><label><input type="checkbox" data-compare-run="${id}"${selection.has(run.id) ? ' checked' : ''}>
                <span class="visually-hidden">${escapeHtml(run.name)} を比較</span></label></td>
            <td><a href="${link}">${escapeHtml(run.name)}</a></td><td>${escapeHtml(KINDS[run.kind])}</td>
            <td>${escapeHtml(run.symbols?.join(', ') ?? run.symbol)}</td>
            <td>${escapeHtml(run.start_date)} ～ ${escapeHtml(run.end_date)}</td>
            <td>${valueText('return_pct', run.summary.return_pct)}</td>
            <td>${valueText('max_drawdown', run.summary.max_drawdown)}</td>
            <td>${valueText('sharpe', run.summary.sharpe)}</td>
            <td>${escapeHtml(new Date(run.created_at).toLocaleString('ja-JP'))}</td>
            <td><button class="btn-secondary btn-sm" type="button" data-rename-run="${id}">名前・メモを編集</button>
                <button class="btn-secondary btn-sm" type="button" data-delete-run="${id}">削除</button></td></tr>`;
    }).join('');
    return `<table class="data-table">${heading(['比較', '名前', '種類', '銘柄', '期間', METRICS.return_pct,
        METRICS.max_drawdown, METRICS.sharpe, '保存日時', '操作'])}<tbody>${rows}</tbody></table>`;
}

export function normalizedComparison(runs) {
    const curves = runs.map(run => {
        const curve = run.result.equity_curve;
        const start = curve[0].equity;
        return new Map(curve.map(row => [row.date, 100 * (row.equity / start)]));
    });
    const labels = [...new Set(curves.flatMap(curve => [...curve.keys()]))].sort();
    return {
        labels,
        datasets: curves.map((curve, index) => ({
            label: runs[index].name,
            data: labels.map(day => curve.get(day) ?? null),
            borderColor: COLORS[index], spanGaps: true, pointRadius: 0,
        })),
    };
}

export function comparisonTable(runs) {
    const rows = Object.entries(METRICS).map(([key, label]) => {
        const values = runs.map(run => run.summary[key]);
        const valid = values.filter(Number.isFinite);
        const best = key === 'max_drawdown' ? Math.min(...valid) : Math.max(...valid);
        return `<tr><th scope="row">${escapeHtml(label)}</th>${values.map(value =>
            `<td>${valueText(key, value)}${RANKED_METRICS.has(key) && Number.isFinite(value) && value === best ? ' <span aria-label="最良">★</span>' : ''}</td>`
        ).join('')}</tr>`;
    }).join('');
    return `<table class="data-table">${heading(['指標', ...runs.map(run => run.name)])}<tbody>${rows}</tbody></table>`;
}

function clearComparison() {
    compareVersion += 1;
    comparison?.destroy();
    comparison = null;
    el('comparison').classList.add('hidden');
    el('compare-summary').textContent = '';
    el('compare-table').innerHTML = '';
}

function updateSelection() {
    el('compare').disabled = selected.size < 2;
    el('saved-table').querySelectorAll('[data-compare-run]').forEach(input => {
        input.disabled = selected.size >= 5 && !input.checked;
    });
}

async function refreshList(onError) {
    const version = ++listVersion;
    const user = state.currentUser;
    try {
        const data = await fetchAPI(root, { cache: 'no-store' });
        if (version !== listVersion || user !== state.currentUser) return;
        records = data.runs;
        selected = new Set(records.filter(run => selected.has(run.id)).map(run => run.id));
        el('saved-table').innerHTML = renderSavedRuns(records, selected);
        updateSelection();
    } catch (error) {
        if (version === listVersion && user === state.currentUser) onError(error.message);
    }
}

export function refreshSavedPanel(signedIn, onError) {
    el('saved').classList.toggle('hidden', !signedIn);
    if (!signedIn) {
        listVersion += 1;
        records = [];
        selected.clear();
        el('saved-table').innerHTML = '';
        el('saved').open = false;
        clearComparison();
        dialog.close(() => { action = null; });
    } else if (el('saved').open) {
        refreshList(onError);
    }
}

function openEditor(mode, data, opener) {
    action = { mode, data, user: state.currentUser };
    const deleting = mode === 'delete';
    el('dialog-title').textContent = { save: 'バックテスト結果を保存', rename: '名前・メモを編集', delete: 'バックテスト結果を削除' }[mode];
    el('dialog-message').textContent = deleting ? `「${data.name}」を削除しますか？`
        : mode === 'save' ? '名前を空欄にすると自動で設定されます。' : '';
    el('dialog-fields').classList.toggle('hidden', deleting);
    el('dialog-name').required = mode === 'rename';
    el('dialog-name').value = data.name ?? '';
    el('dialog-note').value = data.note ?? '';
    el('dialog-error').textContent = '';
    el('dialog-submit').disabled = false;
    el('dialog-submit').textContent = deleting ? '削除' : '保存';
    dialog.open(el(deleting ? 'dialog-cancel' : 'dialog-name'), { opener, fallbackFocus: el('saved').querySelector('summary') });
}

export function bindSavedRuns({ getLatest, onSaved, onRename, onDelete, onError }) {
    const close = () => dialog.close(() => { action = null; });
    dialog.bind(close);
    el('dialog-close').addEventListener('click', close);
    el('dialog-cancel').addEventListener('click', close);
    el('saved').addEventListener('toggle', () => {
        if (el('saved').open && state.currentUser?.access_token) refreshList(onError);
    });
    el('save').addEventListener('click', event => {
        const latest = getLatest();
        if (latest) openEditor('save', latest, event.currentTarget);
    });
    el('saved-table').addEventListener('click', event => {
        const button = event.target.closest('[data-rename-run], [data-delete-run]');
        if (!button) return;
        const { renameRun, deleteRun } = button.dataset;
        const run = records.find(row => row.id === Number(renameRun ?? deleteRun));
        if (run) openEditor(renameRun ? 'rename' : 'delete', run, button);
    });
    el('saved-table').addEventListener('change', event => {
        const input = event.target.closest('[data-compare-run]');
        if (!input) return;
        const id = Number(input.dataset.compareRun);
        if (input.checked && selected.size < 5) selected.add(id);
        else {
            selected.delete(id);
            input.checked = false;
        }
        clearComparison();
        updateSelection();
    });
    el('dialog-form').addEventListener('submit', async event => {
        event.preventDefault();
        if (!action || action.user !== state.currentUser || el('dialog-submit').disabled) return;
        const current = action;
        const { mode, data } = current;
        const path = mode === 'save' ? root : `${root}/${encodeURIComponent(data.id)}`;
        const fields = { name: el('dialog-name').value.trim() || null, note: el('dialog-note').value.trim() || null };
        const options = mode === 'delete' ? { method: 'DELETE' } : {
            method: mode === 'save' ? 'POST' : 'PATCH',
            body: JSON.stringify(mode === 'save' ? { ...data, ...fields } : fields),
        };
        el('dialog-submit').disabled = true;
        el('dialog-error').textContent = '';
        try {
            const row = await fetchAPI(path, options, '保存済みの結果を更新できませんでした');
            if (current.user !== state.currentUser) return;
            if (current === action) close();
            if (mode === 'save') onSaved(data, row);
            if (mode === 'rename') onRename(row);
            if (mode === 'delete') onDelete(data.id);
            clearComparison();
            if (el('saved').open) await refreshList(onError);
        } catch (error) {
            if (current === action) el('dialog-error').textContent = error.message;
        } finally {
            if (current === action) el('dialog-submit').disabled = false;
        }
    });
    el('compare').addEventListener('click', async () => {
        if (selected.size < 2 || selected.size > 5) return;
        clearComparison();
        const version = compareVersion;
        onError('');
        try {
            const runs = await Promise.all(records.filter(run => selected.has(run.id)).map(run =>
                fetchAPI(`${root}/${encodeURIComponent(run.id)}`, { cache: 'no-store' })));
            if (version !== compareVersion) return;
            const data = normalizedComparison(runs);
            el('comparison').classList.remove('hidden');
            el('compare-summary').textContent = '各結果の最初の資産を100とします。日付をそろえ、各結果の期間外は空白にします。★は行内の最良値です。指標は保存時にクライアントが送信した値です。'
                + data.datasets.map(series => ` ${series.label}の最終値 ${valueText('sharpe', series.data.findLast(Number.isFinite))}。`).join('');
            el('compare-table').innerHTML = comparisonTable(runs);
            comparison = new Chart(el('compare-chart'), {
                type: 'line', data, options: { responsive: true, maintainAspectRatio: false, animation: false },
            });
        } catch (error) {
            if (version === compareVersion) onError(error.message);
        }
    });
}
