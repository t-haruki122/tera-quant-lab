"""ニュースサービス - キャッシュ付きニュース取得"""

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import upsert_cache
from app.clients.news_client import NewsClient
from app.clients.yfinance_client import YFinanceClient
from app.config import get_settings
from app.models.db_models import News
from app.singleflight import coalesce, fetch
from app.stats import stats
from app.timeutil import to_db

settings = get_settings()
_news_client = NewsClient()
_yf_client = YFinanceClient()
logger = logging.getLogger("app.services.news")


def _get_company_name(symbol: str) -> str | None:
    return _yf_client.get_company_profile(symbol).get("name")


@coalesce
async def get_news(symbol: str, db: AsyncSession) -> dict:
    """
    ニュースを取得（キャッシュ優先）

    キャッシュTTL: 10分
    """
    symbol = symbol.upper()
    # キャッシュ確認
    cutoff = to_db() - timedelta(seconds=settings.cache_ttl_news)
    stmt = (
        select(News)
        .where(News.symbol == symbol.upper())
        .where(News.cached_at >= cutoff)
        .order_by(News.cached_at.desc())
    )
    result = await db.execute(stmt)
    cached_records = result.scalars().all()

    if cached_records:
        stats.log_cache_hit()
        news_list = [
            {
                "title": r.title,
                "url": r.url,
                "published_at": r.published_at,
            }
            for r in cached_records
        ]
        return {"symbol": symbol.upper(), "news": news_list}

    # 会社名を取得して検索精度を向上
    company_name = None
    try:
        company_name = await fetch(_get_company_name, symbol)
    except Exception:
        pass

    # 外部APIから取得
    stats.log_api_call()
    articles = await fetch(_news_client.get_news, symbol, company_name)

    # キャッシュに保存
    for article in articles:
        values = dict(
            symbol=symbol.upper(),
            title=article["title"],
            url=article["url"],
            published_at=article.get("published_at", ""),
            source=article.get("source", ""),
            cached_at=to_db(),
        )
        await upsert_cache(db, News, values)
    if articles:
        await db.commit()

    news_list = [
        {
            "title": a["title"],
            "url": a["url"],
            "published_at": a.get("published_at"),
        }
        for a in articles
    ]
    return {"symbol": symbol.upper(), "news": news_list}
