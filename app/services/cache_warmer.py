"""Refresh shared quote and USD/JPY caches for stocks on user watchlists."""

import asyncio
import logging
from itertools import batched

from sqlalchemy import select

from app import database
from app.exceptions import RateLimitError
from app.models.db_models import UserListItem
from app.services.forex_service import USD_JPY, refresh_exchange_rate
from app.services.stock_service import refresh_quote

logger = logging.getLogger(__name__)


async def _refresh(symbol: str | None) -> None:
    async with database.AsyncSessionLocal() as db:
        if symbol is None:
            await refresh_exchange_rate(USD_JPY, db)
        else:
            await refresh_quote(symbol, db)


async def warm_cache_once(concurrency: int) -> None:
    refreshed = failed = rate_limited = 0
    error_types: set[str] = set()
    try:
        async with database.AsyncSessionLocal() as db:
            symbols = list((await db.scalars(select(UserListItem.symbol).distinct().order_by(UserListItem.symbol))).all())
        for batch in batched([*symbols, None], concurrency):
            pending = asyncio.gather(*(_refresh(symbol) for symbol in batch), return_exceptions=True)
            try:
                outcomes = await asyncio.shield(pending)
            except asyncio.CancelledError:
                # Drain started work before lifespan disposes the engine. Shared provider flights
                # may also have foreground waiters, so let them finish without scheduling another batch.
                await pending
                raise
            for outcome in outcomes:
                if isinstance(outcome, BaseException):
                    failed += 1
                    rate_limited += isinstance(outcome, RateLimitError)
                    error_types.add(type(outcome).__name__)
                else:
                    refreshed += 1
            if rate_limited:
                break
    except Exception as error:
        failed += 1
        error_types.add(type(error).__name__)
    finally:
        logger.info("Cache warming cycle: refreshed=%d failed=%d rate_limited=%d errors=%s",
                    refreshed, failed, rate_limited, ",".join(sorted(error_types)) or "-")


async def warm_cache_periodically(interval_minutes: int, concurrency: int) -> None:
    while True:
        await warm_cache_once(concurrency)
        await asyncio.sleep(interval_minutes * 60)
