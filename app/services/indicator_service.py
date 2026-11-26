"""指数サービス - キャッシュ付き投資指数取得"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import CacheWindow, cached_response, upsert_cache
from app.clients.yfinance_client import YFinanceClient
from app.config import get_settings
from app.models.db_models import Indicator
from app.singleflight import coalesce, fetch
from app.stats import stats

_client = YFinanceClient()
FIELDS = (
    "roe", "roa", "pbr", "per", "eps", "dividend_yield", "mix_index", "profit_margin", "debt_to_equity",
)


def cached_indicators(record: Indicator) -> dict:
    values = {"symbol": record.symbol, **{field: getattr(record, field) for field in FIELDS}}
    if not (record.per is not None and record.per > 0 and record.pbr is not None and record.pbr > 0):
        values["mix_index"] = None
    return values


@coalesce
async def get_indicators(symbol: str, db: AsyncSession) -> dict:
    """
    投資指数を取得（キャッシュ優先）

    キャッシュTTL: 1日
    """
    window = CacheWindow(get_settings().cache_ttl_history)
    stmt = select(Indicator).where(Indicator.symbol == symbol, Indicator.cached_at >= window.usable_since)
    record = (await db.execute(stmt)).scalar_one_or_none()
    cached = (record.cached_at, cached_indicators(record)) if record is not None else None

    async def refresh() -> dict:
        stats.log_api_call()
        data = await fetch(_client.get_indicators, symbol)
        values = {"symbol": data["symbol"], **{field: data.get(field) for field in FIELDS}}
        await upsert_cache(db, Indicator, values)
        await db.commit()
        return data

    return await cached_response(f"get_indicators:{symbol}", window, cached, refresh)
