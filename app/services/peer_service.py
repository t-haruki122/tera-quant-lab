"""Compare JPX financials or locally cached Yahoo indicators by sector."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median
from weakref import WeakKeyDictionary

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from app.cache import PROVIDER_FAILURES, CacheWindow
from app.clients.yfinance_client import YFinanceClient
from app.config import get_settings
from app.exceptions import SymbolNotFoundError
from app.market_indicators import is_non_company
from app.models.db_models import CompanyProfile, Indicator, JQFinSummary, JQIssue, StockPrice, SymbolDirectoryEntry
from app.schemas.valuation import MetricDirection, PeerCompany, PeerComparison, PeerMetric, SectorSource
from app.services import forex_service
from app.services.jquants_sync import today_jst
from app.services.valuation_math import (
    finite,
    is_japanese_stock,
    jq_actuals,
    jq_annual_rows,
    jq_roe,
    percent,
    positive,
    ratio,
    valuation,
)
from app.singleflight import fetch, flight
from app.stats import stats

_client = YFinanceClient()
_MIN_COUNT = 5
_PRICE_METRICS: tuple[tuple[str, str, MetricDirection], ...] = (
    ("per", "PER", "cheaper"), ("pbr", "PBR", "cheaper"),
    ("mix_index", "ミックス指数", "neutral"), ("dividend_yield", "配当利回り", "better"),
)
_JP_METRICS: tuple[tuple[str, str, MetricDirection], ...] = (
    ("roe", "ROE", "better"), ("operating_margin", "営業利益率", "better"),
    ("equity_ratio", "自己資本比率", "neutral"), ("sales_growth", "売上高成長率", "better"),
)
_YAHOO_METRICS: tuple[tuple[str, str, MetricDirection], ...] = (
    ("roe", "ROE", "better"), ("profit_margin", "利益率", "better"), *_PRICE_METRICS,
)
_FALLBACK_NOTE = "J-Quants 未設定のため、取得済み銘柄のみで比較しています"
_sector_prices: WeakKeyDictionary[AsyncEngine | AsyncConnection, dict[str, tuple[date, dict[str, float]]]] = (
    WeakKeyDictionary()
)


@dataclass
class _Company:
    symbol: str
    name: str | None
    market_cap: float | None
    values: dict[str, float | None]
    as_of: date
    currency: str | None = None


def _metric(
    key: str, label: str, direction: MetricDirection, subject: _Company | None, companies: list[_Company],
) -> PeerMetric:
    value = subject.values.get(key) if subject else None
    data = [v for company in companies if (v := company.values.get(key)) is not None]
    count = len(data)
    rank = percentile = None
    if count >= _MIN_COUNT and value is not None:
        rank = 1 + sum(v > value for v in data)
        percentile = round(100 * (count - rank) / (count - 1), 1)
    return PeerMetric(key=key, label=label, value=value, rank=rank, percentile=percentile,
                      median=median(data) if data else None, count=count, higher_is=direction)


def _comparison(
    symbol: str, sector: str | None, source: SectorSource, companies: list[_Company],
    definitions: tuple[tuple[str, str, MetricDirection], ...], notes: list[str],
    cap_keys: dict[str, float] | None = None,
) -> PeerComparison:
    subject = next((company for company in companies if company.symbol == symbol), None)
    others = [company for company in companies if company.symbol != symbol]
    keys = cap_keys if cap_keys is not None else {c.symbol: c.market_cap or 0 for c in others}
    others.sort(key=lambda company: (-keys.get(company.symbol, 0), company.symbol))
    return PeerComparison(
        symbol=symbol, sector_name=sector, sector_source=source, peer_count=len(companies),
        as_of=max((company.as_of for company in companies), default=None),
        metrics=[_metric(*definition, subject, companies) for definition in definitions],
        peers=[PeerCompany(symbol=c.symbol, name=c.name, market_cap=c.market_cap, **{
            key: c.values.get(key) for key in ("per", "pbr", "roe", "mix_index", "dividend_yield")
        }) for c in others[:10]], notes=notes,
    )


async def _cached_prices(db: AsyncSession, symbols: tuple[str, ...]) -> dict[str, float]:
    settings = get_settings()
    # A quote's market date prevents a stale quote from overriding a newer daily close.
    trading_day = case(
        (StockPrice.interval == "quote", func.coalesce(StockPrice.quote_market_date, func.date(StockPrice.timestamp))),
        else_=func.date(StockPrice.timestamp),
    )
    ranked = select(StockPrice.symbol, StockPrice.close, func.row_number().over(
        partition_by=StockPrice.symbol, order_by=(trading_day.desc(), (StockPrice.interval == "quote").desc(),
                                                 StockPrice.timestamp.desc(), StockPrice.id.desc()),
    ).label("position")).where(
        StockPrice.symbol.in_(symbols), StockPrice.interval.in_(("quote", "1d")),
        StockPrice.cached_at >= CacheWindow(max(settings.cache_ttl_current_price, settings.cache_ttl_history)).usable_since,
        trading_day >= (today_jst() - timedelta(days=7)).isoformat(),
        StockPrice.close > 0,
    ).subquery()
    return {symbol: price for symbol, value in await db.execute(
        select(ranked.c.symbol, ranked.c.close).where(ranked.c.position == 1),
    ) if (price := positive(value)) is not None}


async def _batch_prices(sector: str, missing: tuple[str, ...], db: AsyncSession) -> dict[str, float]:
    bind = db.bind
    assert bind is not None
    cache = _sector_prices.setdefault(bind, {})
    day = today_jst()

    async def once() -> dict[str, float]:
        if (entry := cache.get(sector)) and entry[0] == day:
            return entry[1]
        stats.log_api_call()
        try:
            prices = await fetch(_client.get_batch_closes, missing)
        except (*PROVIDER_FAILURES, SymbolNotFoundError):
            prices = {}
        cache[sector] = (day, prices)
        return prices

    return await flight.run(("peer_sector_prices", bind, sector, day), once)


async def _jp_comparison(symbol: str, db: AsyncSession, prices: bool) -> PeerComparison | None:
    issue_sector = func.nullif(func.trim(JQIssue.sector33_name), "")
    sector = await db.scalar(select(issue_sector).where(JQIssue.symbol == symbol).limit(1))
    if not sector:
        sector = await db.scalar(select(SymbolDirectoryEntry.sector).where(SymbolDirectoryEntry.symbol == symbol))
    if not sector:
        return None
    rows = await db.execute(select(
        JQFinSummary, func.coalesce(JQIssue.name, SymbolDirectoryEntry.name, CompanyProfile.name), CompanyProfile.market_cap,
    ).outerjoin(JQIssue, JQIssue.code5 == JQFinSummary.code5)
        .outerjoin(SymbolDirectoryEntry, SymbolDirectoryEntry.symbol == JQFinSummary.symbol)
        .outerjoin(CompanyProfile, CompanyProfile.symbol == JQFinSummary.symbol)
        .where(func.coalesce(issue_sector, SymbolDirectoryEntry.sector) == sector,
               JQFinSummary.symbol.like("%.T"), SymbolDirectoryEntry.is_etf.is_not(True), jq_actuals()))
    summaries: dict[str, list[JQFinSummary]] = defaultdict(list)
    info = {}
    for row, name, cap in rows:
        if row.symbol and is_japanese_stock(row.symbol):
            db.expunge(row)
            summaries[row.symbol].append(row)
            info[row.symbol] = (name, positive(cap))
    if not summaries:
        return None
    notes = [f"J-Quants のデータは {get_settings().jquants_delay_weeks} 週間遅れです。",
             "最新の通期決算を優先し、通期がない銘柄は取得済みの最新決算を使用しています。"]
    symbols = tuple(sorted(summaries))
    quote_prices = await _cached_prices(db, symbols) if prices else {}
    missing = tuple(ticker for ticker in symbols if ticker not in quote_prices)
    if prices and missing:
        await db.rollback()
        quote_prices = {**await _batch_prices(sector, missing, db), **quote_prices}
        if any(ticker not in quote_prices for ticker in missing):
            notes.append("株価を取得できない銘柄は株価を使う指標の集計から除外しています。")
    companies: list[_Company] = []
    for ticker, statements in summaries.items():
        annual = jq_annual_rows(statements)
        latest = annual[0] if annual else max(statements, key=lambda r: (
            r.period_end or date.min, r.consolidated is True, r.disclosed_date, r.disc_no,
        ))
        previous = annual[1] if len(annual) > 1 else None
        equity_ratio = finite(latest.equity_ratio)
        growth = ratio(latest.sales, previous.sales) if previous else None
        values = {
            "roe": jq_roe(latest), "operating_margin": percent(ratio(latest.operating_profit, latest.sales)),
            "equity_ratio": percent(equity_ratio if equity_ratio is not None else ratio(latest.equity, latest.total_assets)),
            "sales_growth": percent(growth - 1) if growth is not None else None,
        }
        price = quote_prices.get(ticker)
        if prices:
            values.update(valuation(price, latest.eps if annual else None, latest.bps))
            values["dividend_yield"] = percent(ratio(latest.div_annual, price)) if annual else None
        name, cached_cap = info[ticker]
        shares = latest.shares_outstanding_fy
        if shares is not None:
            shares -= latest.treasury_shares_fy or 0
        cap = positive(price * shares) if price and shares and shares > 0 else cached_cap
        companies.append(_Company(ticker, name, cap, values, latest.disclosed_date))
    return _comparison(symbol, sector, "jpx33", companies, _JP_METRICS + (_PRICE_METRICS if prices else ()), notes)


async def _yahoo_comparison(symbol: str, db: AsyncSession, notes: list[str]) -> PeerComparison:
    source: SectorSource = "yahoo_sector"
    profile = await db.scalar(select(CompanyProfile).where(CompanyProfile.symbol == symbol))
    rows = select(CompanyProfile, Indicator).join(Indicator, Indicator.symbol == CompanyProfile.symbol)
    population = []
    sector = profile.sector if profile else None
    if profile and profile.industry:
        industry = [(p, i) for p, i in await db.execute(rows.where(CompanyProfile.industry == profile.industry))
                    if not is_non_company(p.symbol)]
        if len(industry) >= _MIN_COUNT:
            population, sector, source = industry, profile.industry, "yahoo_industry"
    if source == "yahoo_sector" and sector:
        population = [(p, i) for p, i in await db.execute(rows.where(CompanyProfile.sector == sector))
                      if not is_non_company(p.symbol)]
    companies: list[_Company] = []
    for p, i in population:
        values = {key: finite(getattr(i, key)) for key, _, _ in _YAHOO_METRICS}
        for key in ("per", "pbr", "mix_index"):
            values[key] = positive(values[key])
        if values["per"] is None or values["pbr"] is None:
            values["mix_index"] = None
        companies.append(_Company(p.symbol, p.name, positive(p.market_cap), values, i.cached_at.date(), p.currency))
    notes.append(f"取得済みの {len(companies)} 銘柄で比較")
    target_currency = profile.currency if profile else None
    keys = {c.symbol: c.market_cap or 0.0 for c in companies if c.currency == target_currency}
    cross_currency = [c for c in companies if c.currency != target_currency and c.market_cap is not None]
    if cross_currency:
        fx = await forex_service.read_cached_rate(forex_service.USD_JPY, db)
        rate = positive(fx.close) if fx else None
        for company in cross_currency:
            if rate and company.market_cap and {company.currency, target_currency} == {"JPY", "USD"}:
                keys[company.symbol] = company.market_cap * rate if target_currency == "JPY" else company.market_cap / rate
        if any(company.symbol not in keys for company in cross_currency):
            notes.append("時価総額は比較できる通貨の銘柄を優先しています。")
    return _comparison(symbol, sector, source, companies, _YAHOO_METRICS, notes, keys)


async def get_peer_comparison(symbol: str, db: AsyncSession, *, prices: bool = True) -> PeerComparison:
    if is_non_company(symbol):
        return PeerComparison(symbol=symbol, sector_source="yahoo_sector", notes=["企業銘柄が比較対象です。"])
    notes = []
    if is_japanese_stock(symbol):
        if get_settings().jquants_active and (comparison := await _jp_comparison(symbol, db, prices)) is not None:
            return comparison
        notes.append(_FALLBACK_NOTE)
    return await _yahoo_comparison(symbol, db, notes)
