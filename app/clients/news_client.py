"""ニュース取得クライアント"""

import logging
from typing import Any
from urllib.error import HTTPError

from gnews import GNews

from app.clients.errors import external_call
from app.exceptions import SymbolNotFoundError

logger = logging.getLogger("app.external.gnews")


class CheckedGNews(GNews):
    def _fetch_feed(self, url):
        feed = super()._fetch_feed(url)
        status = feed.get("status", 200)
        if status >= 400:
            raise HTTPError(url, status, "News feed HTTP error", {}, None)
        if feed.get("bozo") and not feed.get("entries"):
            raise feed["bozo_exception"]
        return feed


class NewsClient:
    """GNewsライブラリを使用したニュース取得クライアント"""

    def __init__(self):
        self._gnews = CheckedGNews(
            language="en",
            country="US",
            max_results=10,
            max_retries=0,
        )

    @external_call
    def get_news(self, symbol: str, company_name: str | None = None) -> list[dict[str, Any]]:
        """
        指定シンボルに関連するニュースを取得

        Args:
            symbol: ティッカーシンボル
            company_name: 会社名（検索精度向上のため）

        Returns:
            [{"title": str, "url": str, "published_at": str}, ...]
        """
        query = f"{company_name} stock" if company_name else f"{symbol} stock"
        logger.info("External API call: gnews get_news query=%s", query)
        articles = self._gnews.get_news(query)
        if not articles:
            raise SymbolNotFoundError(symbol)
        results = []
        for article in articles:
            results.append({
                "title": article.get("title", ""),
                "url": article.get("url", ""),
                "published_at": article.get("published date", ""),
                "source": article.get("publisher", {}).get("title", "")
                if isinstance(article.get("publisher"), dict)
                else str(article.get("publisher", "")),
            })
        return results
