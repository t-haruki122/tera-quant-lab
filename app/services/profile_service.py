"""プロフィールサービス - キャッシュ付き会社情報取得"""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import upsert_cache
from app.clients.yfinance_client import YFinanceClient
from app.config import get_settings
from app.models.db_models import CompanyProfile
from app.singleflight import coalesce, fetch
from app.stats import stats
from app.timeutil import to_db

settings = get_settings()
_client = YFinanceClient()


@coalesce
async def get_profile(symbol: str, db: AsyncSession) -> dict:
    """
    会社プロフィールを取得（キャッシュ優先）

    キャッシュTTL: 1日
    """
    symbol = symbol.upper()
    # キャッシュ確認
    cutoff = to_db() - timedelta(seconds=settings.cache_ttl_history)
    stmt = (
        select(CompanyProfile)
        .where(CompanyProfile.symbol == symbol.upper())
        .where(CompanyProfile.cached_at >= cutoff)
        .order_by(CompanyProfile.cached_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    cached = result.scalar_one_or_none()

    if cached:
        stats.log_cache_hit()
        return {
            "symbol": cached.symbol,
            "name": cached.name,
            "sector": cached.sector,
            "industry": cached.industry,
            "employees": cached.employees,
            "summary": cached.summary,
            "market_cap": cached.market_cap,
            "currency": cached.currency,
            "website": cached.website,
            "country": cached.country,
        }

    # 外部APIから取得
    stats.log_api_call()
    data = await fetch(_client.get_company_profile, symbol)

    # キャッシュに保存
    values = dict(
        symbol=data["symbol"],
        name=data["name"],
        sector=data.get("sector"),
        industry=data.get("industry"),
        employees=data.get("employees"),
        summary=data.get("summary"),
        market_cap=data.get("market_cap"),
        currency=data.get("currency"),
        website=data.get("website"),
        country=data.get("country"),
        cached_at=to_db(),
    )
    await upsert_cache(db, CompanyProfile, values)
    await db.commit()

    return data
