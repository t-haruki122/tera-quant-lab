/**
 * Stock Analyzer — System stats and dashboard charts
 */

import { API_BASE, HISTORY_MAX_LEN, state, statsHistory } from './state.js';

export async function fetchStats() {
    try {
        const res = await fetch(`${API_BASE}/stats`);
        if (!res.ok) return;
        const data = await res.json();

        // 既存のステータスバーを更新
        document.getElementById('stat-api-calls').textContent = data.api_calls;
        document.getElementById('stat-cache-hits').textContent = data.cache_hits;
        document.getElementById('stat-hit-rate').textContent = data.hit_rate_percent.toFixed(1) + '%';

        const bar = document.getElementById('stats-bar');
        if (data.total_requests > 0) {
            bar.classList.remove('hidden');
        }

        // Stats HistoryとDashboardの更新
        const now = new Date();
        data.timestamp = now.toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

        statsHistory.push(data);
        if (statsHistory.length > HISTORY_MAX_LEN) {
            statsHistory.shift();
        }

        // Dashboard Viewが開かれている場合、ダッシュボードを更新
        if (document.getElementById('stats-view').classList.contains('active')) {
            updateStatsDashboard(data);
        }

    } catch {
        // ignore errors for stats fetching
    }
}

export function updateStatsDashboard(data) {
    // Uptimes
    const minutes = Math.floor(data.uptime_seconds / 60);
    const seconds = data.uptime_seconds % 60;
    document.getElementById('dash-uptime').textContent = `${minutes}m ${seconds}s`;

    // Metrics
    document.getElementById('dash-server-requests').textContent = data.server_requests || 0;
    document.getElementById('dash-cache-hits').textContent = data.cache_hits || 0;
    document.getElementById('dash-avg-time').textContent = (data.avg_response_time_ms || 0).toFixed(1);

    document.getElementById('dash-errors').textContent = data.server_errors || 0;
    document.getElementById('dash-error-rate').textContent = ` (${(data.error_rate_percent || 0).toFixed(1)}%)`;

    // Charts
    updateCharts(data);
}

export function updateCharts(data) {
    const textColor = '#94a3b8';
    const gridColor = 'rgba(255,255,255,0.05)';

    // Hit Rate (Pie Chart)
    const hitCtx = document.getElementById('hitRateChart');
    if (hitCtx) {
        if (!state.hitRateChartInstance) {
            state.hitRateChartInstance = new Chart(hitCtx, {
                type: 'doughnut',
                data: {
                    labels: ['Cache Hits', 'API Calls'],
                    datasets: [{
                        data: [data.cache_hits, data.api_calls],
                        backgroundColor: ['#10b981', '#3b82f6'],
                        borderWidth: 0,
                    }]
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    plugins: { legend: { position: 'bottom', labels: { color: textColor } } }
                }
            });
        } else {
            state.hitRateChartInstance.data.datasets[0].data = [data.cache_hits, data.api_calls];
            state.hitRateChartInstance.update();
        }
    }

    // Endpoints (Bar Chart)
    const epCtx = document.getElementById('endpointChart');
    if (epCtx && data.top_endpoints) {
        const labels = data.top_endpoints.map(e => e.endpoint.substring(0, 15) + (e.endpoint.length > 15 ? '...' : ''));
        const counts = data.top_endpoints.map(e => e.count);

        if (!state.endpointChartInstance) {
            state.endpointChartInstance = new Chart(epCtx, {
                type: 'bar',
                data: {
                    labels: labels,
                    datasets: [{
                        label: 'Hits',
                        data: counts,
                        backgroundColor: '#6366f1',
                        borderRadius: 4,
                    }]
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    plugins: { legend: { display: false } },
                    scales: {
                        x: { ticks: { color: textColor, font: { size: 10 }, maxRotation: 45, minRotation: 45 }, grid: { display: false } },
                        y: { ticks: { color: textColor, precision: 0 }, grid: { color: gridColor, drawBorder: false }, beginAtZero: true }
                    }
                }
            });
        } else {
            state.endpointChartInstance.data.labels = labels;
            state.endpointChartInstance.data.datasets[0].data = counts;
            state.endpointChartInstance.update();
        }
    }

    // Response Time (Line Chart) over Time
    const rtCtx = document.getElementById('responseTimeChart');
    if (rtCtx) {
        const labels = statsHistory.map(h => h.timestamp);
        const avgTimes = statsHistory.map(h => h.avg_response_time_ms || 0);

        if (!state.responseTimeChartInstance) {
            state.responseTimeChartInstance = new Chart(rtCtx, {
                type: 'line',
                data: {
                    labels: labels,
                    datasets: [{
                        label: 'Avg Response Time (ms)',
                        data: avgTimes,
                        borderColor: '#f59e0b',
                        backgroundColor: 'rgba(245, 158, 11, 0.1)',
                        fill: true,
                        tension: 0.4,
                        borderWidth: 2,
                        pointRadius: 2,
                    }]
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    animation: { duration: 0 },
                    plugins: { legend: { display: false } },
                    scales: {
                        x: { ticks: { color: textColor, maxTicksLimit: 6 }, grid: { display: false } },
                        y: { ticks: { color: textColor }, grid: { color: gridColor, drawBorder: false }, beginAtZero: true }
                    }
                }
            });
        } else {
            state.responseTimeChartInstance.data.labels = labels;
            state.responseTimeChartInstance.data.datasets[0].data = avgTimes;
            state.responseTimeChartInstance.update();
        }
    }
}

Object.assign(window, {
    fetchStats,
});
