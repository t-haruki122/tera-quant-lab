"""財務サービス - キャッシュ付き財務データ取得"""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import upsert_cache
from app.clients.yfinance_client import YFinanceClient
from app.config import get_settings
from app.models.db_models import Financial
from app.singleflight import coalesce, fetch
from app.stats import stats
from app.timeutil import to_db

settings = get_settings()
_client = YFinanceClient()


@coalesce
async def get_financials(symbol: str, db: AsyncSession) -> dict:
    """
    財務情報を取得（キャッシュ優先）

    キャッシュTTL: 四半期（約90日）
    """
    symbol = symbol.upper()
    # キャッシュ確認
    cutoff = to_db() - timedelta(seconds=settings.cache_ttl_financials)
    stmt = (
        select(Financial)
        .where(Financial.symbol == symbol.upper())
        .where(Financial.cached_at >= cutoff)
        .order_by(Financial.cached_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    cached = result.scalar_one_or_none()

    if cached:
        stats.log_cache_hit()
        return {
            "symbol": cached.symbol,
            "revenue": cached.revenue,
            "net_income": cached.net_income,
            "eps": cached.eps,
            "pe_ratio": cached.pe_ratio,
        }

    # 外部APIから取得
    stats.log_api_call()
    data = await fetch(_client.get_financials, symbol)

    # キャッシュに保存
    values = dict(
        symbol=data["symbol"],
        revenue=data.get("revenue"),
        net_income=data.get("net_income"),
        eps=data.get("eps"),
        pe_ratio=data.get("pe_ratio"),
        cached_at=to_db(),
    )
    await upsert_cache(db, Financial, values)
    await db.commit()

    return data


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
