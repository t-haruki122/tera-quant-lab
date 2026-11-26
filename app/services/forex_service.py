"""為替レートサービス"""

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import FOREX_TTL_SECONDS, CacheWindow, cached_response, upsert_cache
from app.clients.yfinance_client import YFinanceClient
from app.models.db_models import StockPrice
from app.singleflight import coalesce, fetch
from app.stats import stats
from app.timeutil import to_db, utc_iso

_client = YFinanceClient()

USD_JPY = "USDJPY=X"
FOREX_INTERVAL = "forex"


def cached_rate(record: StockPrice) -> dict:
    metadata = {
        "day_change": record.quote_day_change, "day_change_pct": record.quote_day_change_pct,
        "market_time": record.quote_market_time, "market_date": record.quote_market_date,
    }
    return {
        "pair": record.symbol, "rate": record.close, "timestamp": utc_iso(record.timestamp),
        **{key: value for key, value in metadata.items() if value is not None},
    }


async def read_cached_rate(pair: str, db: AsyncSession) -> StockPrice | None:
    return await db.scalar(
        select(StockPrice)
        .where(StockPrice.symbol == pair, StockPrice.interval == FOREX_INTERVAL)
        .order_by(StockPrice.cached_at.desc(), StockPrice.id.desc())
        .limit(1)
    )


@coalesce
async def get_exchange_rate(pair: str, db: AsyncSession) -> dict:
    """指定された為替ペアの現在のレートを取得（キャッシュ優先）。"""
    window = CacheWindow(FOREX_TTL_SECONDS)
    record = await read_cached_rate(pair, db)
    cached = None
    if record is not None and record.cached_at >= window.usable_since:
        cached = (record.cached_at, cached_rate(record))
    # The shared refresh writes in its own session.
    await db.rollback()
    return await cached_response(f"forex:{pair}", window, cached, lambda: refresh_exchange_rate(pair, db))


@coalesce
async def refresh_exchange_rate(pair: str, db: AsyncSession) -> dict:
    """Refresh the cache for both foreground requests and the warmer."""
    stats.log_api_call()
    data = await fetch(_client.get_exchange_rate, pair)
    timestamp = to_db(datetime.fromisoformat(data["timestamp"]))
    await upsert_cache(db, StockPrice, {
        "symbol": pair, "interval": FOREX_INTERVAL, "timestamp": timestamp, "close": data["rate"],
        "quote_day_change": data.get("day_change"), "quote_day_change_pct": data.get("day_change_pct"),
        "quote_market_time": data.get("market_time"), "quote_market_date": data.get("market_date"),
    })
    await db.execute(delete(StockPrice).where(
        StockPrice.symbol == pair, StockPrice.interval == FOREX_INTERVAL, StockPrice.timestamp != timestamp,
    ))
    await db.commit()
    return data
