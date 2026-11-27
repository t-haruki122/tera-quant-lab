"""Fiscal-year PER/PBR with fiscal statements and nearby cached trading closes."""

from datetime import date, datetime, time, timedelta
from statistics import median
from typing import Any
from weakref import WeakKeyDictionary

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from app.cache import PROVIDER_FAILURES, CacheWindow
from app.clients.yfinance_client import YFinanceClient
from app.config import get_settings
from app.exceptions import SymbolNotFoundError
from app.market_indicators import USD_JPY_SYMBOL, is_non_company
from app.models.db_models import CompanyProfile, Indicator, JQFinSummary, StockPrice
from app.schemas.valuation import ValuationBand, ValuationHistory, ValuationPoint, ValuationRange, ValuationRatios
from app.services import indicator_service, stock_service
from app.services.forex_service import FOREX_INTERVAL, USD_JPY
from app.services.valuation_math import (
    finite,
    is_japanese_stock,
    jq_actuals,
    jq_annual_rows,
    jq_roe,
    positive,
    valuation,
)
from app.singleflight import coalesce, fetch
from app.stats import stats
from app.timeutil import utcnow
from app.validation import currency_code

_client = YFinanceClient()
_MAX_YEARS = 5
_TRADING_WINDOW = timedelta(days=7)
_statements: WeakKeyDictionary[AsyncEngine | AsyncConnection, dict[str, tuple[date, dict[str, Any]]]] = (
    WeakKeyDictionary()
)


async def _yahoo_statements(symbol: str, db: AsyncSession, today: date) -> dict[str, Any]:
    bind = db.bind
    assert bind is not None
    cache = _statements.setdefault(bind, {})
    if (entry := cache.get(symbol)) and entry[0] == today:
        return entry[1]
    stats.log_api_call()
    data = await fetch(_client.get_annual_valuation_statements, symbol)
    if symbol not in cache and len(cache) >= 256:
        del cache[next(iter(cache))]
    cache[symbol] = (today, data)
    return data


async def _fiscal_points(symbol: str, db: AsyncSession, today: date, notes: list[str]) -> tuple[list[ValuationPoint], str | None]:
    jp = is_japanese_stock(symbol)
    points: list[ValuationPoint] = []
    if jp and get_settings().jquants_active:
        rows = await db.scalars(select(JQFinSummary).where(
            JQFinSummary.symbol == symbol, JQFinSummary.period_type == "FY", jq_actuals(),
        ))
        for row in jq_annual_rows(rows):
            if (end := row.period_end or row.fy_end) and end <= today:
                points.append(ValuationPoint(period_end=end, eps=finite(row.eps), bps=finite(row.bps), roe=jq_roe(row),
                                             source="jquants", statement_currency="JPY"))
    yahoo: dict[str, Any] = {}
    if len(points) < _MAX_YEARS:
        try:
            yahoo = await _yahoo_statements(symbol, db, today)
        except (*PROVIDER_FAILURES, SymbolNotFoundError):
            notes.append("Yahoo Finance の年次財務データを取得できませんでした。")
        first_jq = min((p.period_end.replace(day=1) for p in points), default=None)
        for row in yahoo.get("history", []):
            end = date.fromisoformat(row["period_end"])
            if end <= today and (first_jq is None or end < first_jq):
                points.append(ValuationPoint(**row, source="yfinance", statement_currency=yahoo.get("statement_currency")))
    points = sorted(points, key=lambda point: point.period_end)[-_MAX_YEARS:]
    price_currency = "JPY" if jp else currency_code(yahoo.get("price_currency"))
    if not price_currency and points:
        quote = await db.scalar(stock_service.quote_query(symbol).where(StockPrice.quote_currency.is_not(None)))
        price_currency = currency_code(quote.quote_currency) if quote else None
        if not price_currency:
            price_currency = currency_code(await db.scalar(
                select(CompanyProfile.currency).where(CompanyProfile.symbol == symbol),
            ))
    if any(p.source == "jquants" for p in points):
        notes.append(f"J-Quants のデータは {get_settings().jquants_delay_weeks} 週間遅れです。")
    return points, price_currency


async def _fiscal_close(symbol: str, db: AsyncSession, end: date, today: date) -> tuple[date, float] | None:
    last_day = min(today, end + _TRADING_WINDOW)
    row = (await db.execute(select(StockPrice.timestamp, StockPrice.close).where(
        StockPrice.symbol == symbol, StockPrice.interval == "1d", StockPrice.close > 0,
        StockPrice.timestamp >= datetime.combine(end, time.min),
        StockPrice.timestamp <= datetime.combine(last_day, time.max),
    ).order_by(StockPrice.timestamp).limit(1))).first()
    if row and (close := positive(row.close)) is not None:
        return row.timestamp.date(), close
    await db.rollback()
    try:
        data = await stock_service.get_history(symbol, db, end.isoformat(), last_day.isoformat(),
                                               include_technicals=False)
    except (*PROVIDER_FAILURES, SymbolNotFoundError):
        return None
    for bar in sorted(data["history"], key=lambda bar: bar["date"]):
        day = date.fromisoformat(bar["date"])
        if end <= day <= last_day and (close := positive(bar["close"])) is not None:
            return day, close
    return None


async def _currency_multiplier(
    db: AsyncSession, statement: str | None, trading: str | None, day: date,
) -> float | None:
    if not statement or not trading:
        return None
    if statement == trading:
        return 1.0
    if {statement, trading} == {"GBP", "GBp"}:
        return 100.0 if trading == "GBp" else 0.01
    if {statement, trading} != {"JPY", "USD"}:
        return None
    fx = await db.scalar(select(StockPrice.close).where(
        StockPrice.symbol.in_((USD_JPY, USD_JPY_SYMBOL)), StockPrice.interval.in_(("1d", FOREX_INTERVAL)),
        StockPrice.timestamp >= datetime.combine(day - _TRADING_WINDOW, time.min),
        StockPrice.timestamp <= datetime.combine(day, time.max), StockPrice.close > 0,
    ).order_by(StockPrice.timestamp.desc()).limit(1))
    if rate := positive(fx):
        return rate if trading == "JPY" else 1 / rate
    return None


async def _current_ratios(symbol: str, db: AsyncSession) -> ValuationRatios:
    indicator = await db.scalar(select(Indicator).where(Indicator.symbol == symbol))
    window = CacheWindow(get_settings().cache_ttl_history)
    data = indicator_service.cached_indicators(indicator) if indicator and indicator.cached_at >= window.usable_since else {}
    if indicator is None or indicator.cached_at < window.fresh_since:
        await db.rollback()
        try:
            data = await indicator_service.get_indicators(symbol, db)
        except (*PROVIDER_FAILURES, SymbolNotFoundError):
            pass
    per, pbr = positive(data.get("per")), positive(data.get("pbr"))
    return ValuationRatios(per=per, pbr=pbr, mix_index=positive(data.get("mix_index")) if per and pbr else None)


def _band(points: list[ValuationPoint], key: str) -> ValuationRange | None:
    values = [value for point in points if (value := getattr(point, key)) is not None]
    return ValuationRange(min=min(values), median=median(values), max=max(values)) if values else None


@coalesce
async def get_valuation_history(symbol: str, db: AsyncSession) -> ValuationHistory:
    if is_non_company(symbol):
        return ValuationHistory(symbol=symbol, notes=["企業銘柄が対象です。"])
    today = utcnow().date()
    notes: list[str] = []
    points, currency = await _fiscal_points(symbol, db, today, notes)
    if points:
        notes.append("期末付近の終値には、日足履歴と同じ価格調整が反映されます。")
    for point in points:
        close = await _fiscal_close(symbol, db, point.period_end, today)
        if close is None:
            if "期末付近の終値がない年度があります。" not in notes:
                notes.append("期末付近の終値がない年度があります。")
            continue
        point.price_date, point.price = close
        multiplier = await _currency_multiplier(db, point.statement_currency, currency, point.price_date)
        if multiplier is None:
            notes.append(f"{point.period_end} は通貨または当時の為替レートを確認できず、PER・PBRを算出できません。")
            continue
        ratios = valuation(point.price, point.eps * multiplier if point.eps is not None else None,
                           point.bps * multiplier if point.bps is not None else None)
        point.per, point.pbr, point.mix_index = ratios["per"], ratios["pbr"], ratios["mix_index"]
    return ValuationHistory(symbol=symbol, price_currency=currency, points=points, notes=notes,
                            current=await _current_ratios(symbol, db),
                            band=ValuationBand(per=_band(points, "per"), pbr=_band(points, "pbr")))
