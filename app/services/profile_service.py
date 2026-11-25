"""プロフィールサービス - キャッシュ付き会社情報取得"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import CacheWindow, cached_response, upsert_cache
from app.clients.yfinance_client import YFinanceClient
from app.config import get_settings
from app.models.db_models import CompanyProfile
from app.singleflight import coalesce, fetch
from app.stats import stats

_client = YFinanceClient()


@coalesce
async def get_profile(symbol: str, db: AsyncSession) -> dict:
    """
    会社プロフィールを取得（キャッシュ優先）

    キャッシュTTL: 1日
    """
    window = CacheWindow(get_settings().cache_ttl_history)
    stmt = select(CompanyProfile).where(CompanyProfile.symbol == symbol, CompanyProfile.cached_at >= window.usable_since)
    record = (await db.execute(stmt)).scalar_one_or_none()
    fields = (
        "name",
        "sector",
        "industry",
        "employees",
        "summary",
        "market_cap",
        "currency",
        "website",
        "country",
    )
    cached = None
    if record is not None:
        cached = (record.cached_at, {"symbol": record.symbol, **{field: getattr(record, field) for field in fields}})

    async def refresh() -> dict:
        stats.log_api_call()
        data = await fetch(_client.get_company_profile, symbol)
        values = {"symbol": data["symbol"], **{field: data.get(field) for field in fields}}
        await upsert_cache(db, CompanyProfile, values)
        await db.commit()
        return data

    return await cached_response(f"get_profile:{symbol}", window, cached, refresh)
