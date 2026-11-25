/**
 * Stock Analyzer — メインアプリケーションロジック
 */

import { state } from './js/state.js';
import { fetchAPI } from './js/api.js';
import { fetchStats } from './js/stats.js';
import { updateAccountUI, submitAuth, bindAuthEvents } from './js/auth.js';
import { loadDefaultList, submitListAddFromModal, addTagFromInput, setListSearchQuery, bindListEvents, renderListTable } from './js/list.js';
import { searchStock, bindDetailEvents, refreshDetailPrices } from './js/detail.js';
import { bindChartEvents } from './js/chart.js';
import { bindFinancialChartEvents } from './js/financial-chart.js';
import { bindBacktestEvents, openBacktest, refreshBacktestSession, updateBacktestSymbols } from './js/backtest.js';

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
        this.addTagFromInput = deps.addTagFromInput ?? addTagFromInput;
        this.setListSearchQuery = deps.setListSearchQuery ?? setListSearchQuery;
        this.submitAuth = deps.submitAuth ?? submitAuth;
        this.searchStock = deps.searchStock ?? searchStock;
        this.bindAuthEvents = deps.bindAuthEvents ?? bindAuthEvents;
        this.bindListEvents = deps.bindListEvents ?? bindListEvents;
        this.bindDetailEvents = deps.bindDetailEvents ?? bindDetailEvents;
        this.bindChartEvents = deps.bindChartEvents ?? bindChartEvents;
        this.bindFinancialChartEvents = deps.bindFinancialChartEvents ?? bindFinancialChartEvents;
        this.bindBacktestEvents = deps.bindBacktestEvents ?? bindBacktestEvents;
        this.openBacktest = deps.openBacktest ?? openBacktest;

        this.document = deps.documentRef ?? document;
        this.storage = deps.storage ?? localStorage;
        this.setIntervalFn = deps.setIntervalFn ?? window.setInterval.bind(window);
        this.clearIntervalFn = deps.clearIntervalFn ?? window.clearInterval.bind(window);
        this.statsPollingMs = deps.statsPollingMs ?? 10_000;

        this.currentView = 'list';
        this.statsTimerId = null;
    }

    switchView(view, symbol) {
        this.currentView = ['detail', 'stats', 'backtest'].includes(view) ? view : 'list';
        this.document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
        this.document.getElementById(`${this.currentView}-view`)?.classList.add('active');
        for (const name of ['list', 'backtest']) {
            this.document.getElementById(`btn-${name}-view`)?.classList.toggle('active', this.currentView === name);
        }
        this.syncStatsPolling();
        if (this.currentView === 'backtest') this.openBacktest(symbol);
        if (this.currentView === 'detail') refreshDetailPrices();
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
        this.document.getElementById('search-input')?.addEventListener('keydown', e => {
            if (isSubmitEnter(e)) this.searchStock();
        });

        this.document.getElementById('list-search-input')?.addEventListener('input', e => {
            this.setListSearchQuery(e.target.value);
        });

        this.document.getElementById('list-add-modal-input')?.addEventListener('keydown', e => {
            if (isSubmitEnter(e)) this.submitListAddFromModal();
        });

        this.document.getElementById('tag-input')?.addEventListener('keydown', e => {
            if (isSubmitEnter(e)) this.addTagFromInput();
        });

        this.document.getElementById('auth-password')?.addEventListener('keydown', e => {
            if (isSubmitEnter(e)) this.submitAuth();
        });

        this.document.getElementById('logo-home-btn')?.addEventListener('click', () => this.switchView('list'));
        this.document.getElementById('btn-list-view')?.addEventListener('click', () => this.switchView('list'));
        this.document.getElementById('btn-backtest-view')?.addEventListener('click', () => this.switchView('backtest'));
        this.document.getElementById('btn-detail-backtest')?.addEventListener('click', () => this.switchView('backtest', this.state.currentSymbol));
        this.document.addEventListener('visibilitychange', () => this.syncStatsPolling());
        this.document.addEventListener('accountchange', () => {
            if (this.currentView === 'backtest' || !this.state.currentUser) refreshBacktestSession();
        });
        this.document.addEventListener('watchlistchange', () => {
            if (this.currentView === 'backtest') updateBacktestSymbols();
        });

        this.bindAuthEvents();
        this.bindListEvents();
        this.bindDetailEvents();
        this.bindChartEvents();
        this.bindFinancialChartEvents();
        this.bindBacktestEvents();
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
        await Promise.all([this.restoreUserSession(), this.fetchExchangeRate()]);
    }
}

export function switchView(view) {
    if (!activeApp) return;
    activeApp.switchView(view);
}

activeApp = new App();
window.switchView = view => switchView(view);
activeApp.init();
