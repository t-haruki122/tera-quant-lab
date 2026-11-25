"""SQLite cache upserts and bounded, cache-only retention cleanup."""

import time
from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy.dialects.sqlite import insert

from app.config import get_settings
from app.models.db_models import CompanyProfile, Financial, Indicator, News, StockPrice
from app.timeutil import to_db

CACHE_KEYS = {
    StockPrice: ("symbol", "timestamp", "interval"),
    Financial: ("symbol",),
    News: ("symbol", "url"),
    CompanyProfile: ("symbol",),
    Indicator: ("symbol",),
}
_last_purge: dict[str, float] = {}


async def purge_cache(db, *, force=False):
    key = str(db.bind.url)
    now = time.monotonic()
    if not force and now - _last_purge.get(key, float("-inf")) < 3600:
        return
    # Reserve before yielding so concurrent writers cannot all run the purge.
    previous = _last_purge.get(key)
    _last_purge[key] = now
    try:
        cutoff = to_db() - timedelta(days=get_settings().cache_retention_days)
        for model in CACHE_KEYS:
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
