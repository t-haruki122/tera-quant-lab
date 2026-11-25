"""財務サービス - キャッシュ付き財務データ取得"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import CacheWindow, cached_response, upsert_cache
from app.clients.yfinance_client import YFinanceClient
from app.config import get_settings
from app.models.db_models import Financial
from app.singleflight import coalesce, fetch
from app.stats import stats

_client = YFinanceClient()


@coalesce
async def get_financials(symbol: str, db: AsyncSession) -> dict:
    """
    財務情報を取得（キャッシュ優先）

    キャッシュTTL: 四半期（約90日）
    """
    window = CacheWindow(get_settings().cache_ttl_financials)
    stmt = select(Financial).where(Financial.symbol == symbol, Financial.cached_at >= window.usable_since)
    record = (await db.execute(stmt)).scalar_one_or_none()
    fields = (
        "revenue",
        "net_income",
        "eps",
        "pe_ratio",
    )
    cached = None
    if record is not None:
        cached = (record.cached_at, {"symbol": record.symbol, **{field: getattr(record, field) for field in fields}})

    async def refresh() -> dict:
        stats.log_api_call()
        data = await fetch(_client.get_financials, symbol)
        values = {"symbol": data["symbol"], **{field: data.get(field) for field in fields}}
        await upsert_cache(db, Financial, values)
        await db.commit()
        return data

    return await cached_response(f"get_financials:{symbol}", window, cached, refresh)


@coalesce
async def get_financial_history(symbol: str, db: AsyncSession, limit: int = 6) -> dict:
    """過去の年次財務データを取得"""
    symbol = symbol.upper()
    # 財務履歴は呼び出し頻度が低く、銘柄ごとに件数も少ないため都度取得とする
    # （必要に応じて将来DBキャッシュ化）
    del db
    stats.log_api_call()
    return await fetch(_client.get_financial_history, symbol, limit=limit)


@coalesce
async def get_dividend_history(symbol: str, db: AsyncSession, limit: int = 20) -> dict:
    """過去の配当データ（1株配当・配当利回り）を取得"""
    symbol = symbol.upper()
    del db
    stats.log_api_call()
    return await fetch(_client.get_dividend_history, symbol, limit=limit)
