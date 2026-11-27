import { escapeHtml, formatLargeNumber } from './utils.js';

const byId = id => document.getElementById(id);
const periods = [['1m', '1か月'], ['3m', '3か月'], ['6m', '6か月'], ['1y', '1年'], ['3y', '3年'], ['full', '設定来']];
const number = (value, options) => Number.isFinite(value) ? value.toLocaleString('ja-JP', options) : '—';
const yen = value => Number.isFinite(value) ? `${number(value)} 円` : '—';
const signed = value => number(value, { signDisplay: 'exceptZero' });
const pct = value => Number.isFinite(value) ? `${number(value)}%` : '—';
const extreme = point => point?.nav == null ? '—' : `${yen(point.nav)}${point.date ? `（${point.date}）` : ''}`;
let chart = null;

export function resetFundChart() {
    chart?.destroy();
    chart = null;
    byId('fund-chart-container').classList.add('hidden');
    byId('fund-chart-summary').textContent = '';
    byId('fund-chart-status').textContent = '';
}

export function renderFundData(data) {
    byId('stock-meta').textContent = `協会コード ${data.association_code}`;
    byId('price-display').textContent = `基準価額 ${yen(data.nav)}（1万口あたり）`;
    byId('price-timestamp').textContent = `基準日 ${data.base_date}`;
    const change = Number.isFinite(data.change) ? `${signed(data.change)} 円` : '—';
    const changePct = Number.isFinite(data.change_pct) ? `${signed(data.change_pct)}%` : '—';
    byId('fund-change').textContent = `前日比 ${change}（${changePct}）`;
    const assets = data.net_assets == null ? '—' : formatLargeNumber(data.net_assets, 'JPY');
    byId('fund-assets').textContent = `純資産総額 ${assets}`;
    byId('fund-source').textContent = data.source_credit;

    const stats = data.stats;
    const rows = periods.map(([key, label]) => `<tr><th scope="row">${label}</th>${[
        pct(stats.returns[key]), pct(stats.risk[key]), number(stats.risk_return[key]),
        extreme(stats.max_nav[key]), extreme(stats.min_nav[key]),
    ].map(value => `<td class="numeric">${escapeHtml(value)}</td>`).join('')}</tr>`).join('');
    byId('fund-stats-body').innerHTML = rows;

    const { history } = data;
    if (history.length < 2) {
        byId('fund-chart-status').textContent = '日々の基準価額を蓄積中です';
        return;
    }
    const first = history[0];
    const latest = history.at(-1);
    const summary = `${first.base_date} ${yen(first.nav)}、${latest.base_date} ${yen(latest.nav)}（${history.length}件）`;
    byId('fund-chart-summary').textContent = summary;
    if (typeof globalThis.Chart !== 'function') {
        byId('fund-chart-status').textContent = summary;
        return;
    }
    byId('fund-chart-container').classList.remove('hidden');
    chart = new Chart(byId('fund-chart').getContext('2d'), {
        type: 'line',
        data: {
            labels: history.map(row => row.base_date),
            datasets: [{ label: '基準価額', data: history.map(row => row.nav), borderColor: '#60a5fa' }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false }, tooltip: { callbacks: { label: point => yen(point.parsed.y) } } },
            scales: {
                x: { ticks: { color: '#94a3b8', maxTicksLimit: 8, maxRotation: 0 } },
                y: { ticks: { color: '#94a3b8', callback: yen } },
            },
        },
    });
}
