"""ニュースサービスと既存キャッシュの読み取り時の正規化。"""

import logging
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import TypedDict
from unicodedata import normalize
from urllib.parse import parse_qsl, urlsplit

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import PROVIDER_FAILURES, CacheWindow, cached_response, purge_cache
from app.clients.news_client import NewsClient
from app.config import get_settings
from app.exceptions import SymbolNotFoundError
from app.models.db_models import News
from app.services.profile_service import get_profile
from app.singleflight import coalesce, fetch
from app.stats import stats
from app.timeutil import to_db, utc_iso

_news_client = NewsClient()
logger = logging.getLogger(__name__)


class NormalizedArticle(TypedDict):
    title: str
    url: str
    published_at: datetime | None
    publisher: str | None


def parse_published_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError:
            parsed = parsedate_to_datetime(value.strip())
        return to_db(parsed)
    except (ValueError, OverflowError):
        logger.debug("Could not parse news published_at: %r", value)
        return None


def _url_key(value: str) -> tuple | None:
    try:
        url = urlsplit(value)
        if url.scheme not in {"http", "https"} or not url.hostname:
            return None
        port = url.port or {"http": 80, "https": 443}[url.scheme]
    except ValueError:
        return None
    params = sorted(
        (key, value) for key, value in parse_qsl(url.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"gclid", "fbclid", "msclkid"}
    )
    return url.scheme, url.hostname, port, url.path.rstrip("/"), tuple(params)


def _text_key(value: str) -> str:
    return " ".join(normalize("NFKC", value).casefold().split())


def normalize_articles(articles: list[dict]) -> list[NormalizedArticle]:
    """Keep the newest usable article for each URL or title/publisher pair."""
    dated = [(parse_published_at(article.get("published_at")), article) for article in articles]
    dated.sort(key=lambda item: (item[0] is not None, item[0] or datetime.min), reverse=True)
    results: list[NormalizedArticle] = []
    seen_urls = set()
    seen_titles = set()
    for published_at, article in dated:
        url = (article.get("url") or "").strip()
        url_key = _url_key(url)
        if url_key is None:
            continue
        title = (article.get("title") or "").strip()
        publisher = (article.get("publisher") or "").strip()
        if publisher:
            title = title.removesuffix(f" - {publisher}").rstrip()
        title_key = (_text_key(title), _text_key(publisher))
        if url_key in seen_urls or (all(title_key) and title_key in seen_titles):
            continue
        seen_urls.add(url_key)
        if all(title_key):
            seen_titles.add(title_key)
        results.append({
            "title": title,
            "url": url,
            "published_at": published_at,
            "publisher": publisher or None,
        })
    return results


def _news_response(symbol: str, articles: list[NormalizedArticle]) -> dict:
    return {"symbol": symbol, "news": [
        {
            **article,
            "published_at": utc_iso(article["published_at"]) if article["published_at"] is not None else None,
            "source": article["publisher"],  # Keep the original API field for existing clients.
        }
        for article in articles
    ]}


@coalesce
async def get_news(symbol: str, db: AsyncSession) -> dict:
    """ニュースを取得（キャッシュ優先）。旧形式の日時も読み取り時に変換する。"""
    window = CacheWindow(get_settings().cache_ttl_news)
    stmt = (
        select(News)
        .where(News.symbol == symbol, News.cached_at >= window.usable_since)
        .order_by(News.cached_at.desc(), News.id.desc())
    )
    rows = (await db.scalars(stmt)).all()
    cached = None
    if rows:
        fresh = [row for row in rows if row.cached_at >= window.fresh_since]
        articles = normalize_articles([
            {"title": row.title, "url": row.url, "published_at": row.published_at, "publisher": row.source}
            for row in fresh or rows
        ])
        cached_at = (fresh or rows)[-1].cached_at
        cached = (cached_at, _news_response(symbol, articles))
    await db.rollback()

    async def refresh() -> dict:
        profile = {}
        if not symbol.endswith(".T"):
            try:
                profile = await get_profile(symbol, db)
            except (*PROVIDER_FAILURES, SymbolNotFoundError):
                pass
        stats.log_api_call()
        articles = normalize_articles(await fetch(_news_client.get_news, symbol, profile.get("name"), profile.get("country")))
        if articles:
            await purge_cache(db)
            await db.execute(delete(News).where(News.symbol == symbol))
            cached_at = to_db()
            db.add_all([
                News(
                    symbol=symbol, title=article["title"], url=article["url"], source=article["publisher"],
                    published_at=published_at.isoformat() if (published_at := article["published_at"]) else None,
                    cached_at=cached_at,
                )
                for article in articles
            ])
            await db.commit()
        return _news_response(symbol, articles)

    return await cached_response(f"news:{symbol}", window, cached, refresh)
