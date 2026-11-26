"""既存の株価・為替キャッシュを使った経済指標一覧。"""

import asyncio
import logging
from collections.abc import Awaitable
from dataclasses import asdict
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import PROVIDER_FAILURES, CacheWindow
from app.config import get_settings
from app.exceptions import SymbolNotFoundError
from app.market_indicators import INDICATORS, USD_JPY_SYMBOL, MarketIndicator
from app.models.db_models import StockPrice
from app.services import stock_service
from app.services.investment_quotes import finite
from app.timeutil import utc_iso, utcnow

logger = logging.getLogger(__name__)
SPARKLINE_SIZE = 30


def _indicator_fields(indicator: MarketIndicator, quote: dict, history: dict, *, refresh: bool) -> dict:
    closes = {
        row["date"]: close for row in history.get("history", [])
        if (close := finite(row["close"])) is not None
    }
    days = sorted(closes)
    value = finite(quote.get("price"))
    change = finite(quote.get("day_change")) if value is not None else None
    previous = finite(value - change) if value is not None and change is not None else None
    if value is not None and previous is None and (market_date := quote.get("market_date")):
        # A current-session bar may already be present. Use a strictly earlier close.
        previous = next((closes[day] for day in reversed(days) if day < market_date), None)
        if previous is not None:
            change = finite(value - previous)
    if indicator.unit == "yield_pct":
        comparison = {"change_pt": change}
    else:
        change_pct = finite(quote.get("day_change_pct")) if value is not None else None
        if change_pct is None and change is not None and previous:
            change_pct = finite(change / abs(previous) * 100)
        comparison = {"change_pct": change_pct}

    problems = []
    if refresh:
        if value is None or quote.get("stale"):
            problems.append("現在値を更新できませんでした。")
        if not days or history.get("stale"):
            problems.append("日次履歴を更新できませんでした。")
    return {
        **asdict(indicator),
        "value": value,
        "previous_close": previous,
        "change": change,
        **comparison,
        "as_of": quote.get("market_time") or quote.get("timestamp"),
        "sparkline": [closes[day] for day in days[-SPARKLINE_SIZE:]],
        "stale": bool(quote.get("stale") or history.get("stale")),
        "error": " ".join(problems) or None,
    }


async def _cached_indicator(
    indicator: MarketIndicator, db: AsyncSession, quote_window: CacheWindow, history_window: CacheWindow,
) -> dict:
    quote = await stock_service.read_cached_usd_jpy_quote(db) if indicator.symbol == USD_JPY_SYMBOL else None
    if not quote and (row := await db.scalar(stock_service.quote_query(indicator.symbol))) is not None:
        quote = {**stock_service.cached_quote(row), **quote_window.metadata(row.cached_at)}
    rows = (await db.scalars(
        select(StockPrice)
        .where(StockPrice.symbol == indicator.symbol, StockPrice.interval == "1d")
        .order_by(StockPrice.timestamp.desc())
        .limit(SPARKLINE_SIZE)
    )).all()
    history = {"history": [{"date": row.timestamp.date().isoformat(), "close": row.close} for row in rows]}
    if rows:
        history.update(history_window.metadata(min(row.cached_at for row in rows)))
    return _indicator_fields(indicator, quote or {}, history, refresh=False)


async def _lookup(source: str, symbol: str, operation: Awaitable[dict]) -> dict:
    try:
        return await operation
    except (*PROVIDER_FAILURES, SymbolNotFoundError):
        return {}
    except Exception:
        logger.exception("Market %s lookup failed for %s", source, symbol)
        return {}


async def get_market_overview(db: AsyncSession, *, cached: bool) -> dict:
    settings = get_settings()
    if cached:
        quote_window = CacheWindow(settings.cache_ttl_current_price)
        history_window = CacheWindow(settings.cache_ttl_history)
        indicators = [
            await _cached_indicator(indicator, db, quote_window, history_window)
            for indicator in INDICATORS
        ]
    else:
        bind = db.bind
        await db.rollback()
        slots = asyncio.Semaphore(settings.watchlist_summary_concurrency)
        start = (utcnow().date() - timedelta(days=59)).isoformat()

        async def refresh(indicator: MarketIndicator) -> dict:
            async with slots, AsyncSession(bind=bind) as session:
                symbol = indicator.symbol
                quote = await _lookup("quote", symbol, stock_service.get_current_price(symbol, session))
                history = await _lookup("history", symbol, stock_service.get_history(
                    symbol, session, start, include_technicals=False, min_cached_bars=SPARKLINE_SIZE,
                ))
                return _indicator_fields(indicator, quote, history, refresh=True)

        indicators = await asyncio.gather(*(refresh(indicator) for indicator in INDICATORS))
    return {"as_of": utc_iso(), "indicators": indicators}
