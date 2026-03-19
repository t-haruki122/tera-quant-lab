/**
 * Stock Analyzer — メインアプリケーションロジック
 */

import { state } from './js/state.js';
import { fetchAPI } from './js/api.js';
import { fetchStats } from './js/stats.js';
import { updateAccountUI, submitAuth, bindAuthEvents } from './js/auth.js';
import { loadDefaultList, submitListAddFromModal, addTagFromInput, setListSearchQuery, bindListEvents } from './js/list.js';
import { searchStock, bindDetailEvents } from './js/detail.js';
import { bindChartEvents } from './js/chart.js';
import { bindFinancialChartEvents } from './js/financial-chart.js';

let activeApp = null;

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

        this.document = deps.documentRef ?? document;
        this.storage = deps.storage ?? localStorage;
        this.setIntervalFn = deps.setIntervalFn ?? window.setInterval.bind(window);
        this.statsPollingMs = deps.statsPollingMs ?? 5000;

        this.boundSwitchView = this.switchView.bind(this);
        this.statsTimerId = null;
    }

    switchView(view) {
        this.document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
        const listBtn = this.document.getElementById('btn-list-view');
        if (listBtn) listBtn.classList.remove('active');

        if (view === 'detail') {
            this.document.getElementById('detail-view')?.classList.add('active');
            return;
        }

        if (view === 'stats') {
            this.document.getElementById('stats-view')?.classList.add('active');
            this.fetchStats();
            return;
        }

        this.document.getElementById('list-view')?.classList.add('active');
        if (listBtn) listBtn.classList.add('active');
    }

    async fetchExchangeRate() {
        try {
            const res = await this.fetchAPI('/forex/usdjpy', { method: 'GET' });
            this.state.exchangeRateUSDJPY = res.rate;
        } catch (err) {
            console.warn('Failed to fetch exchange rate:', err);
        }
    }

    bindCoreEvents() {
        this.document.getElementById('search-input')?.addEventListener('keydown', e => {
            if (e.key === 'Enter') this.searchStock();
        });

        this.document.getElementById('list-search-input')?.addEventListener('input', e => {
            this.setListSearchQuery(e.target.value);
        });

        this.document.getElementById('list-add-modal-input')?.addEventListener('keydown', e => {
            if (e.key === 'Enter') this.submitListAddFromModal();
        });

        this.document.getElementById('tag-input')?.addEventListener('keydown', e => {
            if (e.key === 'Enter') this.addTagFromInput();
        });

        this.document.getElementById('auth-password')?.addEventListener('keydown', e => {
            if (e.key === 'Enter') this.submitAuth();
        });

        this.document.getElementById('logo-home-btn')?.addEventListener('click', () => this.switchView('list'));
        this.document.getElementById('btn-list-view')?.addEventListener('click', () => this.switchView('list'));

        this.bindAuthEvents();
        this.bindListEvents();
        this.bindDetailEvents();
        this.bindChartEvents();
        this.bindFinancialChartEvents();
    }

    async restoreUserSession() {
        const saved = this.storage.getItem('stockAnalyzerUser');
        if (!saved) return;

        try {
            this.state.currentUser = JSON.parse(saved);
            this.updateAccountUI();
            await this.loadDefaultList();
        } catch {
            // ignore invalid local cache
        }
    }

    startStatsPolling() {
        this.fetchStats();
        this.statsTimerId = this.setIntervalFn(this.fetchStats, this.statsPollingMs);
    }

    async init() {
        this.bindCoreEvents();
        await this.restoreUserSession();
        await this.fetchExchangeRate();
        this.startStatsPolling();
    }
}

export function switchView(view) {
    if (!activeApp) return;
    activeApp.switchView(view);
}

activeApp = new App();
window.switchView = view => switchView(view);
activeApp.init();
