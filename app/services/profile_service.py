"""プロフィールサービス - キャッシュ付き会社情報取得"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import PROVIDER_FAILURES, CacheWindow, cached_response, upsert_cache
from app.clients.yfinance_client import YFinanceClient
from app.config import get_settings
from app.exceptions import SymbolNotFoundError
from app.models.db_models import CompanyProfile, StockPrice
from app.services import stock_service
from app.singleflight import coalesce, fetch
from app.stats import stats
from app.validation import currency_code

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


async def get_trading_currency(symbol: str, db: AsyncSession) -> str | None:
    """Use cached metadata first, prioritizing the trading quote."""
    settings = get_settings()
    quote = await db.scalar(
        select(StockPrice.quote_currency)
        .where(StockPrice.symbol == symbol, StockPrice.interval == "quote",
               StockPrice.cached_at >= CacheWindow(settings.cache_ttl_current_price).usable_since,
               StockPrice.quote_currency.is_not(None), StockPrice.quote_currency != "")
        .order_by(StockPrice.cached_at.desc(), StockPrice.id.desc()).limit(1)
    )
    if code := currency_code(quote):
        return code
    profile = await db.scalar(
        select(CompanyProfile.currency)
        .where(CompanyProfile.symbol == symbol,
               CompanyProfile.cached_at >= CacheWindow(settings.cache_ttl_history).usable_since)
    )
    if code := currency_code(profile):
        return code
    for service in (stock_service.get_current_price, get_profile):
        try:
            data = await service(symbol, db)
        except (*PROVIDER_FAILURES, SymbolNotFoundError):
            continue
        if code := currency_code(data.get("currency")):
            return code
    return None
