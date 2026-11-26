import logging
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock

import feedparser
import pytest
from sqlalchemy import select

from app import database
from app.clients.news_client import CheckedGNews, NewsClient, news_locale
from app.config import Settings
from app.exceptions import SymbolNotFoundError
from app.models.db_models import News
from app.services import news_service
from app.timeutil import to_db


@pytest.mark.parametrize(("symbol", "country", "name", "locale", "query"), [
    ("7203.t", "United States", "Toyota Motor Corporation", ("ja", "JP"), "7203 株価"),
    ("9984.T", None, "9984.T", ("ja", "JP"), "9984 株価"),
    ("130A.T", None, None, ("ja", "JP"), "130A 株価"),
    ("TM", " Japan ", "トヨタ自動車", ("ja", "JP"), "TM 株価"),
    ("TM", "日本", None, ("ja", "JP"), "TM 株価"),
    ("AAPL", "United States", "Apple Inc.", ("en", "US"), "Apple Inc. stock"),
    ("SHOP.TO", "Canada", None, ("en", "US"), "SHOP.TO stock"),
])
def test_news_locale_and_query(monkeypatch, settings, symbol, country, name, locale, query):
    assert news_locale(symbol, country) == locale
    settings.news_period = "30d"
    calls = []

    def search(self, term):
        calls.append((self.language, self.country, self.period, term))
        return [{"publisher": {"href": "https://finance.yahoo.co.jp", "title": "Yahoo!ファイナンス"}}]

    monkeypatch.setattr(CheckedGNews, "get_news", search)
    assert NewsClient().get_news(symbol, name, country)[0]["publisher"] == "Yahoo!ファイナンス"
    assert calls == [(*locale, "30d", query)]


@pytest.mark.parametrize(("configured", "results", "expected"), [
    ("7d", [[], [{"title": "Fallback"}]], ["7d", None]),
    ("7d", [[], []], ["7d", None]),
    ("", [[{"title": "Unlimited"}]], [None]),
    ("", [[]], [None]),
])
def test_news_retries_an_empty_limited_search_once(monkeypatch, settings, configured, results, expected):
    monkeypatch.delenv("NEWS_PERIOD", raising=False)
    assert Settings(_env_file=None).news_period == "30d"
    monkeypatch.setenv("NEWS_PERIOD", configured)
    settings.news_period = Settings(_env_file=None).news_period
    calls = []
    answers = iter(results)

    def search(self, query):
        calls.append((self.period, query))
        return next(answers)

    monkeypatch.setattr(CheckedGNews, "get_news", search)
    if results[-1]:
        assert NewsClient().get_news("AAPL", "Apple Inc.")[0]["title"] == results[-1][0]["title"]
    else:
        with pytest.raises(SymbolNotFoundError):
            NewsClient().get_news("AAPL", "Apple Inc.")
    assert calls == [(period, "Apple Inc. stock") for period in expected]


def test_published_date_formats(caplog):
    parse = news_service.parse_published_at
    assert parse("Tue, 25 Nov 2026 10:00:00 GMT") == datetime(2026, 11, 25, 10)
    assert parse("2026-11-25T19:00:00+09:00") == datetime(2026, 11, 25, 10)
    assert parse("2026-11-25T10:00:00Z") == datetime(2026, 11, 25, 10)
    assert parse("2026-11-25T10:00:00") == datetime(2026, 11, 25, 10)
    with caplog.at_level(logging.DEBUG, logger=news_service.__name__):
        assert parse("garbage") is None
    assert "Could not parse news published_at: 'garbage'" in caplog.text


def test_gnews_adapter_leaves_redirects_intact():
    url = "https://news.google.com/rss/articles/abc?oc=5"
    assert CheckedGNews()._process({"link": url, "source": {"title": "Publisher"}})["url"] == url


ARTICLES = [
    {"title": "Original", "url": "https://example.test/article/", "published_at": "2026-11-24T10:00:00Z"},
    {"title": "Current", "url": "https://example.test/article?utm_source=x#fragment",
     "published_at": "Tue, 25 Nov 2026 10:00:00 GMT", "source": "Example"},
    {"title": "Older URL duplicate", "url": "https://example.test/article/?utm_content=x",
     "published_at": "Wed, 25 Nov 2026 10:30:00 +0100", "source": "Example"},
    {"title": " Breaking   ＮＥＷＳ ", "source": " NEWSroom ", "url": "https://example.test/other",
     "published_at": "2026-11-24T10:00:00Z"},
    {"title": "Breaking News", "source": "Newsroom", "url": "https://news.google.com/rss/articles/abc?oc=5",
     "published_at": "2026-11-25T20:00:00+09:00"},
    {"title": "Redirect URL duplicate", "url": "https://news.google.com/rss/articles/abc/?utm_campaign=copy&oc=5#section",
     "published_at": "2026-11-25T10:59:00Z"},
    {"title": "Breaking News", "source": "Other publisher", "url": "http://example.test/unknown-date",
     "published_at": "garbage"},
    {"title": "Invalid scheme", "url": "javascript:alert(1)", "published_at": "2026-11-26T10:00:00Z"},
    {"title": "Relative URL", "url": "/article", "published_at": "2026-11-26T11:00:00Z"},
]
EXPECTED = [
    {"title": "Breaking News", "source": "Newsroom", "publisher": "Newsroom",
     "url": "https://news.google.com/rss/articles/abc?oc=5",
     "published_at": "2026-11-25T11:00:00Z"},
    {"title": "Current", "source": "Example", "publisher": "Example",
     "url": "https://example.test/article?utm_source=x#fragment",
     "published_at": "2026-11-25T10:00:00Z"},
    {"title": "Breaking News", "source": "Other publisher", "publisher": "Other publisher",
     "url": "http://example.test/unknown-date",
     "published_at": None},
]


def test_news_is_cleaned_before_storage_and_legacy_rows_are_cleaned_on_read(client, monkeypatch):
    profile = AsyncMock(return_value={"name": "トヨタ自動車", "country": "Japan"})
    provider = Mock(return_value=[{**article, "publisher": article.get("source")} for article in ARTICLES])
    monkeypatch.setattr(news_service, "get_profile", profile)
    monkeypatch.setattr(news_service._news_client, "get_news", provider)

    async def seed_legacy():
        now = to_db() - timedelta(seconds=1)
        async with database.AsyncSessionLocal() as db:
            db.add_all([News(symbol="LEGACY", cached_at=now, **article) for article in ARTICLES])
            await db.commit()

    async def stored_dates(symbol):
        async with database.AsyncSessionLocal() as db:
            return (await db.scalars(select(News.published_at).where(News.symbol == symbol))).all()

    client.portal.call(seed_legacy)
    legacy = client.get("/stock/LEGACY/news")
    assert legacy.status_code == 200
    assert legacy.json() == {"symbol": "LEGACY", "news": EXPECTED}
    provider.assert_not_called()
    profile.assert_not_called()
    assert "Tue, 25 Nov 2026 10:00:00 GMT" in client.portal.call(stored_dates, "LEGACY")

    fresh = client.get("/stock/7203.T/news")
    assert fresh.status_code == 200
    assert fresh.json() == {"symbol": "7203.T", "news": EXPECTED}
    provider.assert_called_once_with("7203.T", None, None)
    profile.assert_not_called()
    assert set(client.portal.call(stored_dates, "7203.T")) == {"2026-11-25T11:00:00", "2026-11-25T10:00:00", None}
    assert client.get("/stock/7203.T/news").json() == fresh.json()
    assert provider.call_count == 1


def test_google_rss_publisher_is_cached_and_only_matching_title_suffix_is_removed(client, monkeypatch, settings):
    from gnews import GNews

    settings.news_period = "30d"
    rss = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><title>Google News</title>
      <item><title>トヨタ株価、午後一段高 … - 日本経済新聞</title>
        <link>https://news.google.com/rss/articles/abc</link>
        <source url="https://www.nikkei.com">日本経済新聞</source>
        <pubDate>Wed, 25 Nov 2026 10:00:00 GMT</pubDate>
      </item>
      <item><title>トヨタ株価 - 午前の取引</title>
        <link>https://news.google.com/rss/articles/def</link>
        <source url="https://finance.yahoo.co.jp">Yahoo!ファイナンス</source>
      </item>
      <item><title>トヨタ株価、午後一段高 …</title>
        <link>https://news.google.com/rss/articles/duplicate</link>
        <source url="https://www.nikkei.com">日本経済新聞</source>
      </item>
    </channel></rss>""".encode()
    parsed = feedparser.parse(rss)
    parsed["status"] = 200
    assert parsed.entries[0].source == {"href": "https://www.nikkei.com", "title": "日本経済新聞"}
    feed = Mock(return_value=parsed)
    monkeypatch.setattr(GNews, "_fetch_feed", feed)
    data = client.get("/stock/7203.T/news").json()
    assert [(row["title"], row["publisher"], row["source"]) for row in data["news"]] == [
        ("トヨタ株価、午後一段高 …", "日本経済新聞", "日本経済新聞"),
        ("トヨタ株価 - 午前の取引", "Yahoo!ファイナンス", "Yahoo!ファイナンス"),
    ]
    assert client.get("/stock/7203.T/news").json() == data
    feed.assert_called_once()
    url = feed.call_args.args[0]
    assert "7203%20株価%20when%3A30d" in url
    assert "&hl=ja&gl=JP&ceid=JP:ja" in url
