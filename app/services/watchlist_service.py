"""Aggregate the cached data sources used by the watchlist table."""

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ExternalAPIError, RateLimitError, SymbolNotFoundError, json_safe
from app.models.db_models import StockMemo, UserListItem
from app.services import indicator_service, profile_service, stock_service, user_service

logger = logging.getLogger(__name__)


async def get_list_memos(user_id: int, list_id: int, db: AsyncSession) -> dict[str, str | None]:
    rows = await db.execute(
        select(StockMemo.symbol, StockMemo.memo)
        .join(UserListItem, UserListItem.symbol == StockMemo.symbol)
        .where(StockMemo.user_id == user_id, UserListItem.list_id == list_id)
    )
    return {symbol: memo for symbol, memo in rows}


async def get_list_summary(user_id: int, list_id: int, db: AsyncSession, concurrency: int) -> dict:
    data = await user_service.get_list_detail(user_id, list_id, db)
    if not data["items"]:
        return data
    memos = await get_list_memos(user_id, list_id, db)
    bind = db.bind
    # Release the request's read connection before running cache lookups and writes.
    await db.rollback()
    slots = asyncio.Semaphore(concurrency)

    async def lookup(service, symbol):
        try:
            async with AsyncSession(bind=bind) as session:
                return await service(symbol, session)
        except (ExternalAPIError, RateLimitError, SymbolNotFoundError):
            return None
        except Exception:
            logger.exception("Watchlist lookup failed for %s", symbol)
            return None

    async def summarize(item):
        symbol = item["symbol"]
        async with slots:
            quote, profile, indicators = await asyncio.gather(
                lookup(stock_service.get_current_price, symbol),
                lookup(profile_service.get_profile, symbol),
                lookup(indicator_service.get_indicators, symbol),
            )
        fields: dict = {"memo": memos.get(symbol)}
        if quote is not None:
            fields["price"] = quote.get("price")
        if profile is not None:
            fields.update({key: profile.get(key) for key in ("name", "country", "sector", "currency", "market_cap")})
        if indicators is not None:
            fields.update({key: indicators.get(key) for key in ("mix_index", "dividend_yield", "per", "pbr")})
        stale_times = [
            source["cached_at"] for source in (quote, profile, indicators)
            if source is not None and source.get("stale")
        ]
        if stale_times:
            fields.update(stale=True, cached_at=min(stale_times))
        if quote is None or profile is None or indicators is None:
            fields["error"] = (
                "銘柄データを取得できませんでした。"
                if quote is profile is indicators is None
                else "一部のデータを取得できませんでした。"
            )
        return json_safe({**item, **fields})

    data["items"] = await asyncio.gather(*(summarize(item) for item in data["items"]))
    return data
