"""SQLite cache upserts and bounded, cache-only retention cleanup."""

import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete
from sqlalchemy.dialects.sqlite import insert

from app.config import get_settings
from app.exceptions import ExternalAPIError, RateLimitError
from app.models.db_models import CompanyProfile, Financial, Indicator, News, StockPrice
from app.stats import stats
from app.timeutil import to_db, utc_iso

logger = logging.getLogger(__name__)
PROVIDER_FAILURES = (ExternalAPIError, RateLimitError)
FOREX_TTL_SECONDS = 3600

CACHE_KEYS = {
    StockPrice: ("symbol", "timestamp", "interval"),
    Financial: ("symbol",),
    News: ("symbol", "url"),
    CompanyProfile: ("symbol",),
    Indicator: ("symbol",),
}
_last_purge: dict[str, float] = {}


class CacheWindow:
    def __init__(self, ttl_seconds: int):
        self.fresh_since = to_db() - timedelta(seconds=ttl_seconds)
        self.usable_since = self.fresh_since - timedelta(days=get_settings().cache_stale_max_days)

    def metadata(self, cached_at: datetime) -> dict:
        return {"stale": cached_at < self.fresh_since, "cached_at": utc_iso(cached_at)}


def stale_fields(key: str, cached_at: datetime, error: Exception) -> dict[str, Any]:
    logger.warning("Serving stale cache for %s (cached_at=%s): %s", key, utc_iso(cached_at), error)
    stats.log_cache_hit()
    return {"stale": True, "cached_at": utc_iso(cached_at)}


def allows_stale(error: ExternalAPIError | RateLimitError) -> bool:
    return isinstance(error, RateLimitError) or error.allow_stale


async def cached_response(
    key: str,
    window: CacheWindow,
    cached: tuple[datetime, dict] | None,
    refresh: Callable[[], Awaitable[dict]],
) -> dict:
    if cached is not None and cached[0] >= window.fresh_since:
        stats.log_cache_hit()
        return cached[1]
    try:
        return await refresh()
    except PROVIDER_FAILURES as error:
        if not allows_stale(error) or cached is None or cached[0] < window.usable_since:
            raise
        return {**cached[1], **stale_fields(key, cached[0], error)}


async def purge_cache(db, *, force=False):
    key = str(db.bind.url)
    now = time.monotonic()
    if not force and now - _last_purge.get(key, float("-inf")) < 3600:
        return
    # Reserve before yielding so concurrent writers cannot all run the purge.
    previous = _last_purge.get(key)
    _last_purge[key] = now
    try:
        settings = get_settings()
        cached_at = to_db()
        retention = timedelta(days=settings.cache_retention_days)
        stale_window = timedelta(days=settings.cache_stale_max_days)
        ttls = {
            StockPrice: max(settings.cache_ttl_current_price, settings.cache_ttl_history, FOREX_TTL_SECONDS),
            Financial: settings.cache_ttl_financials,
            News: settings.cache_ttl_news,
            CompanyProfile: settings.cache_ttl_history,
            Indicator: settings.cache_ttl_history,
        }
        for model, ttl in ttls.items():
            cutoff = cached_at - max(retention, timedelta(seconds=ttl) + stale_window)
            await db.execute(delete(model).where(model.cached_at < cutoff))
    except BaseException:
        if previous is None:
            _last_purge.pop(key, None)
        else:
            _last_purge[key] = previous
        raise


async def upsert_cache(db, model, values):
    await purge_cache(db)
    values = dict(values)
    values["symbol"] = values["symbol"].upper()
    values["cached_at"] = to_db()
    if "timestamp" in values:
        values["timestamp"] = to_db(values["timestamp"])
    stmt = insert(model).values(**values)
    updates = {key: getattr(stmt.excluded, key) for key in values if key != "id"}
    await db.execute(stmt.on_conflict_do_update(index_elements=CACHE_KEYS[model], set_=updates))
