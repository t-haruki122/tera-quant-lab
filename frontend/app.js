/**
 * Stock Analyzer — メインアプリケーションロジック
 */

import { state } from './js/state.js';
import { fetchAPI } from './js/api.js';
import { fetchStats } from './js/stats.js';
import { updateAccountUI, submitAuth, bindAuthEvents } from './js/auth.js';
import { loadDefaultList, submitListAddFromModal, setListSearchQuery, bindListEvents, renderListTable } from './js/list.js';
import { searchStock, loadStock, bindDetailEvents, refreshDetailPrices, refreshDetailHistory } from './js/detail.js';
import { bindChartEvents, updatePeriodControls } from './js/chart.js';
import { bindComparisonEvents } from './js/comparison.js';
import { parseRoute, serializeRoute } from './js/router.js';
import { bindFinancialChartEvents } from './js/financial-chart.js';
import { bindBacktestEvents, openBacktest, refreshBacktestSession, updateBacktestSymbols } from './js/backtest.js';
import { bindAlertDialogEvents, closeAlerts, showAlertCheckResult } from './js/alert-dialog.js';
import { createAlertPolling } from './js/alerts.js';
import { bindSymbolCombobox } from './js/symbol-combobox.js';

let activeApp = null;

function isSubmitEnter(event) {
    return event.key === 'Enter' && !event.isComposing && event.keyCode !== 229;
}

export class App {
    constructor(deps = {}) {
        this.state = deps.state ?? state;
        this.fetchAPI = deps.fetchAPI ?? fetchAPI;
        this.fetchStats = deps.fetchStats ?? fetchStats;
        this.updateAccountUI = deps.updateAccountUI ?? updateAccountUI;
        this.loadDefaultList = deps.loadDefaultList ?? loadDefaultList;
        this.submitListAddFromModal = deps.submitListAddFromModal ?? submitListAddFromModal;
        this.setListSearchQuery = deps.setListSearchQuery ?? setListSearchQuery;
        this.submitAuth = deps.submitAuth ?? submitAuth;
        this.searchStock = deps.searchStock ?? searchStock;
        this.loadStock = deps.loadStock ?? loadStock;
        this.refreshDetailHistory = deps.refreshDetailHistory ?? refreshDetailHistory;
        this.bindAuthEvents = deps.bindAuthEvents ?? bindAuthEvents;
        this.bindListEvents = deps.bindListEvents ?? bindListEvents;
        this.bindDetailEvents = deps.bindDetailEvents ?? bindDetailEvents;
        this.bindChartEvents = deps.bindChartEvents ?? bindChartEvents;
        this.bindComparisonEvents = deps.bindComparisonEvents ?? bindComparisonEvents;
        this.bindFinancialChartEvents = deps.bindFinancialChartEvents ?? bindFinancialChartEvents;
        this.bindBacktestEvents = deps.bindBacktestEvents ?? bindBacktestEvents;
        this.openBacktest = deps.openBacktest ?? openBacktest;
        this.bindAlertDialogEvents = deps.bindAlertDialogEvents ?? bindAlertDialogEvents;
        this.closeAlerts = deps.closeAlerts ?? closeAlerts;

        this.document = deps.documentRef ?? document;
        this.window = deps.windowRef ?? window;
        this.storage = deps.storage ?? localStorage;
        this.setIntervalFn = deps.setIntervalFn ?? window.setInterval.bind(window);
        this.clearIntervalFn = deps.clearIntervalFn ?? window.clearInterval.bind(window);
        this.statsPollingMs = deps.statsPollingMs ?? 10_000;
        this.alertPolling = deps.alertPolling ?? createAlertPolling({
            getUser: () => this.state.currentUser, isHidden: () => this.document.hidden,
            request: this.fetchAPI, onResult: showAlertCheckResult,
        });

        this.currentView = 'list';
        this.route = null;
        this.detailUser = null;
        this.statsTimerId = null;
    }

    switchView(view, symbol, period) {
        if (view === 'detail') {
            symbol ??= this.state.currentSymbol;
            period ??= this.state.currentPeriod;
        }
        const hash = serializeRoute({ view, symbol, period });
        if (this.window.location && this.window.location.hash !== hash) this.window.location.hash = hash;
        this.renderRoute(parseRoute(hash));
    }

    applyLocation() {
        const { location, history } = this.window;
        const hash = location?.hash ?? '';
        const route = parseRoute(hash);
        if (hash && hash !== '#/' && route.view === 'list') {
            history.replaceState(history.state, '', '#/');
        }
        this.renderRoute(route);
    }

    renderRoute(route) {
        const { view, symbol, period, run } = route;
        if (this.route?.view === view && this.route.symbol === symbol && this.route.period === period && this.route.run === run) return;
        const changedView = this.route && this.route.view !== view;
        this.route = route;
        this.currentView = view;
        this.document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
        this.document.getElementById(`${this.currentView}-view`)?.classList.add('active');
        for (const name of ['list', 'backtest']) {
            const button = this.document.getElementById(`btn-${name}-view`);
            button?.classList.toggle('active', view === name);
            if (view === name) button?.setAttribute('aria-current', 'page');
            else button?.removeAttribute('aria-current');
        }
        if (changedView) this.document.getElementById('main-content')?.focus();
        this.syncStatsPolling();
        if (changedView) this.closeAlerts();
        if (this.currentView === 'backtest') this.openBacktest(symbol, { run });
        if (this.currentView === 'detail') {
            const periodChanged = period !== this.state.currentPeriod;
            this.state.currentPeriod = period;
            updatePeriodControls(period);
            if (symbol !== this.state.currentSymbol || this.detailUser !== this.state.currentUser) this.loadDetail(symbol);
            else if (periodChanged) this.refreshDetailHistory();
            refreshDetailPrices();
        }
    }

    loadDetail(symbol) {
        this.detailUser = this.state.currentUser;
        this.loadStock(symbol);
    }

    async fetchExchangeRate() {
        try {
            const { rate, stale, cached_at } = await this.fetchAPI('/forex/usdjpy', { anonymous: true });
            const cachedAt = stale ? cached_at : null;
            if (Number.isFinite(rate) && rate > 0 &&
                (rate !== this.state.exchangeRateUSDJPY || cachedAt !== this.state.exchangeRateCachedAt)) {
                this.state.exchangeRateUSDJPY = rate;
                this.state.exchangeRateCachedAt = cachedAt;
                renderListTable();
                if (this.currentView === 'detail') refreshDetailPrices();
            }
        } catch (err) {
            console.warn('Failed to fetch exchange rate:', err);
        }
    }

    bindCoreEvents() {
        this.window.addEventListener?.('hashchange', () => this.applyLocation());
        this.document.getElementById('skip-link')?.addEventListener('click', e => {
            e.preventDefault();
            this.document.getElementById('main-content')?.focus();
        });
        bindSymbolCombobox(this.document.getElementById('search-input'),
            this.document.getElementById('search-symbol-options'), { onSubmit: this.searchStock, request: this.fetchAPI });

        this.document.getElementById('list-search-input')?.addEventListener('input', e => {
            this.setListSearchQuery(e.target.value);
        });

        bindSymbolCombobox(this.document.getElementById('list-add-modal-input'),
            this.document.getElementById('list-add-symbol-options'),
            { onSubmit: this.submitListAddFromModal, request: this.fetchAPI });

        this.document.getElementById('auth-password')?.addEventListener('keydown', e => {
            if (isSubmitEnter(e)) this.submitAuth();
        });

        this.document.getElementById('logo-home-btn')?.addEventListener('click', () => this.switchView('list'));
        this.document.getElementById('btn-list-view')?.addEventListener('click', () => this.switchView('list'));
        this.document.getElementById('btn-backtest-view')?.addEventListener('click', () => this.switchView('backtest'));
        this.document.getElementById('btn-detail-backtest')?.addEventListener('click', () => this.switchView('backtest', this.state.currentSymbol));
        this.document.querySelectorAll('.period-btn[data-period]').forEach(button => {
            button.addEventListener('click', () => this.switchView('detail', this.state.currentSymbol, button.dataset.period));
        });
        this.document.addEventListener('visibilitychange', () => {
            this.syncStatsPolling();
            this.alertPolling.sync();
        });
        this.document.addEventListener('accountchange', () => {
            this.alertPolling.sync();
            this.closeAlerts();
            if (this.currentView === 'backtest' || !this.state.currentUser) refreshBacktestSession();
            if (this.currentView === 'detail' && this.route?.symbol) this.loadDetail(this.route.symbol);
        });
        this.document.addEventListener('watchlistchange', () => {
            if (this.currentView === 'backtest') updateBacktestSymbols();
        });
        this.bindAuthEvents();
        this.bindListEvents();
        this.bindDetailEvents();
        this.bindChartEvents();
        this.bindComparisonEvents();
        this.bindFinancialChartEvents();
        this.bindBacktestEvents();
        this.bindAlertDialogEvents();
    }

    async restoreUserSession() {
        try {
            const saved = this.storage.getItem('stockAnalyzerUser');
            if (!saved) return;
            this.state.currentUser = JSON.parse(saved);
            this.updateAccountUI();
            await this.loadDefaultList();
        } catch {
            // ignore invalid local cache
        }
    }

    syncStatsPolling() {
        const visible = this.currentView === 'stats' && !this.document.hidden;
        if (visible && this.statsTimerId === null) {
            this.fetchStats();
            this.statsTimerId = this.setIntervalFn(() => {
                if (this.currentView === 'stats' && !this.document.hidden) this.fetchStats();
            }, this.statsPollingMs);
        } else if (!visible && this.statsTimerId !== null) {
            this.clearIntervalFn(this.statsTimerId);
            this.statsTimerId = null;
        }
    }

    async init() {
        this.bindCoreEvents();
        const session = this.restoreUserSession();
        this.applyLocation();
        await Promise.all([session, this.fetchExchangeRate()]);
    }
}

export function switchView(view, symbol, period) {
    activeApp?.switchView(view, symbol, period);
}

activeApp = new App();
window.switchView = switchView;
activeApp.init();
