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
    volumeChartInstance: null,
    macdChartInstance: null,
    financialChartInstance: null,
    dividendChartInstance: null,
    currentFinancialHistory: null,
    currentFinancialMetric: 'net_income',
    currentPeriod: '3mo',
    currentChartHistory: [],
    currentChartCurrency: null,
    latestChartRequestId: 0,

    hitRateChartInstance: null,
    endpointChartInstance: null,
    responseTimeChartInstance: null,

    exchangeRateUSDJPY: null,
    exchangeRateCachedAt: null,

    listItems: [],
    listCash: null,
    listPortfolio: null,
    sortKey: 'symbol',
    sortAsc: true,

    currentUser: null,
    currentListId: null,
    watchlists: [],
    defaultListId: null,

    pendingDeleteSymbol: null,
    pendingDeleteTimer: null,

    listSearchQuery: '',

    authMode: 'login',
};
