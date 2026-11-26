"""Bounded, isolated lookups for portfolio valuation and alert checks."""

import asyncio
import logging
from math import isfinite

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from app.cache import FOREX_TTL_SECONDS, PROVIDER_FAILURES, CacheWindow
from app.exceptions import SymbolNotFoundError
from app.services import forex_service, stock_service

logger = logging.getLogger(__name__)
type Bind = AsyncEngine | AsyncConnection | None


def finite(value: float | None) -> float | None:
    return value if value is not None and isfinite(value) else None


async def validate_symbol(symbol: str, db: AsyncSession) -> None:
    # Quote refreshes write to the cache in their own session.
    await db.rollback()
    try:
        await stock_service.get_current_price(symbol, db)
    except PROVIDER_FAILURES:
        pass


async def fetch_quotes(symbols: set[str], bind: Bind, concurrency: int) -> dict[str, dict | None]:
    slots = asyncio.Semaphore(concurrency)

    async def lookup(symbol: str) -> tuple[str, dict | None]:
        async with slots:
            try:
                async with AsyncSession(bind=bind) as db:
                    return symbol, await stock_service.get_current_price(symbol, db)
            except (*PROVIDER_FAILURES, SymbolNotFoundError):
                return symbol, None
            except Exception:
                logger.exception("Quote lookup failed for %s", symbol)
                return symbol, None

    return dict(await asyncio.gather(*(lookup(symbol) for symbol in symbols)))


async def fetch_usd_jpy(bind: Bind) -> dict:
    try:
        async with AsyncSession(bind=bind) as db:
            return await forex_service.get_exchange_rate(forex_service.USD_JPY, db)
    except (*PROVIDER_FAILURES, SymbolNotFoundError):
        return {}
    except Exception:
        logger.exception("USD/JPY lookup failed")
        return {}


async def cached_usd_jpy(db: AsyncSession) -> dict:
    row = await forex_service.read_cached_rate(forex_service.USD_JPY, db)
    if row is None:
        return {}
    return {"rate": row.close, **CacheWindow(FOREX_TTL_SECONDS).metadata(row.cached_at)}
