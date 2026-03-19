/**
 * Stock Analyzer — Shared mutable application state
 */

export const API_BASE = '';
export const CACHE_TTL = 5 * 60 * 1000;
export const HISTORY_MAX_LEN = 20;

export const cache = new Map();
export const statsHistory = [];

export const state = {
    currentSymbol: null,
    priceChart: null,
    rsiChartInstance: null,
    financialChartInstance: null,
    dividendChartInstance: null,
    currentFinancialHistory: [],
    currentFinancialMetric: 'net_income',
    currentPeriod: '3mo',
    currentChartHistory: [],
    currentChartCurrency: null,
    latestChartRequestId: 0,

    hitRateChartInstance: null,
    endpointChartInstance: null,
    responseTimeChartInstance: null,

    exchangeRateUSDJPY: null,

    listItems: [],
    sortKey: 'symbol',
    sortAsc: true,

    currentUser: null,
    currentListId: null,

    pendingDeleteSymbol: null,
    pendingDeleteTimer: null,

    activeTagFilter: null,
    listSearchQuery: '',
    tagEditSymbol: null,

    authMode: 'login',
};
