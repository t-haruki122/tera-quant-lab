"""ニュース取得クライアント"""

import logging
from collections.abc import Mapping
from email.message import Message
from typing import Any
from urllib.error import HTTPError

from gnews import GNews

from app.clients.errors import external_call
from app.config import get_settings
from app.exceptions import SymbolNotFoundError

logger = logging.getLogger("app.external.gnews")


def news_locale(symbol: str, profile_country: str | None = None) -> tuple[str, str]:
    country = (profile_country or "").strip().casefold()
    if symbol.upper().endswith(".T") or country in {"japan", "jp", "jpn", "日本", "日本国"}:
        return "ja", "JP"
    return "en", "US"


def news_query(symbol: str, company_name: str | None, language: str) -> str:
    if language == "ja":
        return f"{symbol.removesuffix('.T')} 株価"
    name = (company_name or "").strip()
    return f"{name or symbol} stock"


class CheckedGNews(GNews):
    def _fetch_feed(self, url):
        feed = super()._fetch_feed(url)
        status = feed.get("status", 200)
        if status >= 400:
            raise HTTPError(url, status, "News feed HTTP error", Message(), None)
        if feed.get("bozo") and not feed.get("entries"):
            raise feed["bozo_exception"]
        return feed

    def _process(self, item: dict) -> dict | None:
        # Preserve the RSS link, including Google News redirects, without fetching the article.
        if url := item.get("link"):
            return {
                "title": item.get("title", ""),
                "url": url,
                "published date": item.get("published"),
                "publisher": item.get("source"),
            }
        return None


class NewsClient:
    """GNewsライブラリを使用したニュース取得クライアント"""

    @external_call
    def get_news(
        self, symbol: str, company_name: str | None = None, profile_country: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        指定シンボルに関連するニュースを取得

        Args:
            symbol: ティッカーシンボル
            company_name: 会社名（検索精度向上のため）
            profile_country: 会社プロフィールの国

        Returns:
            タイトル、URL、公開日時、配信元を含む記事の一覧
        """
        language, country = news_locale(symbol, profile_country)
        query = news_query(symbol, company_name, language)
        period = get_settings().news_period.strip() or None

        def search(period: str | None) -> list[dict]:
            logger.info("External API call: gnews get_news language=%s country=%s query=%s period=%s",
                        language, country, query, period)
            client = CheckedGNews(language=language, country=country, period=period, max_results=10, max_retries=0)
            return client.get_news(query)

        articles = search(period)
        if not articles and period is not None:
            articles = search(None)
        if not articles:
            raise SymbolNotFoundError(symbol)
        results = []
        for article in articles:
            publisher = article.get("publisher")
            if isinstance(publisher, Mapping):
                publisher = publisher.get("title")
            results.append({
                "title": article.get("title", ""),
                "url": article.get("url", ""),
                "published_at": article.get("published date", ""),
                "publisher": publisher if isinstance(publisher, str) else None,
            })
        return results
