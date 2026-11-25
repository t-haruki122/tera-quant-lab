"""指数サービス - キャッシュ付き投資指数取得"""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import upsert_cache
from app.clients.yfinance_client import YFinanceClient
from app.config import get_settings
from app.models.db_models import Indicator
from app.singleflight import coalesce, fetch
from app.stats import stats
from app.timeutil import to_db

settings = get_settings()
_client = YFinanceClient()


@coalesce
async def get_indicators(symbol: str, db: AsyncSession) -> dict:
    """
    投資指数を取得（キャッシュ優先）

    キャッシュTTL: 1日
    """
    symbol = symbol.upper()
    # キャッシュ確認
    cutoff = to_db() - timedelta(seconds=settings.cache_ttl_history)
    stmt = (
        select(Indicator)
        .where(Indicator.symbol == symbol.upper())
        .where(Indicator.cached_at >= cutoff)
        .order_by(Indicator.cached_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    cached = result.scalar_one_or_none()

    if cached:
        stats.log_cache_hit()
        return {
            "symbol": cached.symbol,
            "roe": cached.roe,
            "roa": cached.roa,
            "pbr": cached.pbr,
            "per": cached.per,
            "eps": cached.eps,
            "dividend_yield": cached.dividend_yield,
            "mix_index": cached.mix_index,
            "profit_margin": cached.profit_margin,
            "debt_to_equity": cached.debt_to_equity,
        }

    # 外部APIから取得
    stats.log_api_call()
    data = await fetch(_client.get_indicators, symbol)

    # キャッシュに保存
    values = dict(
        symbol=data["symbol"],
        roe=data.get("roe"),
        roa=data.get("roa"),
        pbr=data.get("pbr"),
        per=data.get("per"),
        eps=data.get("eps"),
        dividend_yield=data.get("dividend_yield"),
        mix_index=data.get("mix_index"),
        profit_margin=data.get("profit_margin"),
        debt_to_equity=data.get("debt_to_equity"),
        cached_at=to_db(),
    )
    await upsert_cache(db, Indicator, values)
    await db.commit()

    return data
