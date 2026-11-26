import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from curl_cffi import CurlError
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import database, main
from app.cache import FOREX_TTL_SECONDS
from app.clients import errors, yfinance_client
from app.exceptions import (
    UPSTREAM_ERROR,
    UPSTREAM_RATE_LIMIT,
    UPSTREAM_UNAVAILABLE,
    ExternalAPIError,
    RateLimitError,
    SymbolNotFoundError,
    UpstreamUnavailableError,
)
from app.models.db_models import CompanyProfile, Financial, Indicator, News, StockPrice
from app.services import finance_service, forex_service, indicator_service, news_service, profile_service, stock_service
from app.timeutil import to_db, utc_iso


@pytest.mark.parametrize(("path", "provider", "method", "model", "ttl", "data"), [
    ("/stock/TEST", stock_service._client, "get_current_price", StockPrice, "cache_ttl_current_price",
     {"symbol": "TEST", "price": 33, "timestamp": "2026-01-01T10:00:00Z"}),
    ("/stock/TEST/history", stock_service._client, "get_history", StockPrice, "cache_ttl_history",
     [{"date": "2026-01-01", "close": 10}]),
    ("/stock/TEST/financials", finance_service._client, "get_financials", Financial, "cache_ttl_financials",
     {"symbol": "TEST", "revenue": 123}),
    ("/stock/TEST/profile", profile_service._client, "get_company_profile", CompanyProfile, "cache_ttl_history",
     {"symbol": "TEST", "name": "Company"}),
    ("/stock/TEST/indicators", indicator_service._client, "get_indicators", Indicator, "cache_ttl_history",
     {"symbol": "TEST", "per": 15}),
    ("/stock/TEST/news", news_service._news_client, "get_news", News, "cache_ttl_news",
     [{"title": "Article", "url": "https://example.test/story", "published_at": "garbage"}]),
    ("/forex/usdjpy", forex_service._client, "get_exchange_rate", StockPrice, None,
     {"pair": "USDJPY=X", "rate": 150, "timestamp": "2026-01-01T10:00:00Z"}),
])
def test_stale_fallback_and_existing_errors(client, settings, monkeypatch, path, provider, method, model, ttl, data):
    monkeypatch.setattr(news_service, "get_profile", AsyncMock(return_value={}))
    fake = Mock()
    monkeypatch.setattr(provider, method, fake)
    ttl_seconds = getattr(settings, ttl) if ttl is not None else FOREX_TTL_SECONDS
    symbol = "USDJPY=X" if provider is forex_service._client else "TEST"

    async def set_cached_at(cached_at):
        async with database.AsyncSessionLocal() as db:
            await db.execute(update(model).where(model.symbol == symbol).values(cached_at=cached_at))
            await db.commit()

    failures = [
        (ExternalAPIError("HTTP 500"), 502, UPSTREAM_ERROR),
        (UpstreamUnavailableError("timeout"), 503, UPSTREAM_UNAVAILABLE),
        (RateLimitError("limited"), 429, UPSTREAM_RATE_LIMIT),
    ]
    for failure, expected, detail in failures:
        fake.side_effect = failure
        response = client.get(path)
        assert response.status_code == expected
        assert response.json() == {"detail": detail, "status_code": expected}
        assert str(failure) not in response.text
        if expected == 429:
            assert response.headers["Retry-After"] == "60"

    fake.side_effect = None
    fake.return_value = data
    response = client.get(path)
    assert response.status_code == 200
    fresh = response.json()
    assert "stale" not in fresh and "cached_at" not in fresh
    fake.reset_mock()
    fake.side_effect = UpstreamUnavailableError()
    assert client.get(path).json() == fresh
    fake.assert_not_called()

    expired = to_db() - timedelta(seconds=ttl_seconds + 10)
    client.portal.call(set_cached_at, expired)
    for failure, _, _ in failures:
        fake.side_effect = failure
        response = client.get(path)
        assert response.status_code == 200
        assert response.json() == {**fresh, "stale": True, "cached_at": utc_iso(expired)}
    assert fake.call_count == len(failures)

    fake.side_effect = SymbolNotFoundError("TEST")
    assert client.get(path).status_code == 404
    fake.side_effect = ExternalAPIError("malformed provider payload", allow_stale=False)
    assert client.get(path).status_code == 502

    fake.side_effect = UpstreamUnavailableError()
    client.portal.call(set_cached_at, expired - timedelta(days=settings.cache_stale_max_days))
    assert client.get(path).status_code == 503


def test_forex_stale_fallback_survives_a_database_reopen_and_application_restart(monkeypatch, async_db, settings, caplog):
    monkeypatch.setattr(main, "settings", settings)
    ticker = Mock(return_value=SimpleNamespace(info={"regularMarketPrice": 150}))
    monkeypatch.setattr(yfinance_client.yf, "Ticker", ticker)
    monkeypatch.setattr(errors.time, "sleep", Mock())
    expired = to_db() - timedelta(hours=2)

    async def expire_rate():
        async with database.AsyncSessionLocal() as db:
            await db.execute(update(StockPrice).where(
                StockPrice.symbol == "USDJPY=X", StockPrice.interval == forex_service.FOREX_INTERVAL,
            ).values(cached_at=expired))
            await db.commit()

    with TestClient(main.app) as first:
        saved = first.get("/forex/usdjpy")
        assert saved.status_code == 200
        first.portal.call(expire_rate)
    ticker.assert_called_once_with("USDJPY=X")

    raw = "Failed to connect to query2.finance.yahoo.com:443 over proxy 127.0.0.1"
    ticker.side_effect = CurlError(raw, code=7)
    reopened = create_async_engine(settings.database_url)
    monkeypatch.setattr(database, "engine", reopened)
    monkeypatch.setattr(database, "AsyncSessionLocal", async_sessionmaker(reopened, expire_on_commit=False))
    with TestClient(main.app) as restarted:
        stale = restarted.get("/forex/usdjpy")
        assert stale.status_code == 200
        assert stale.json() == {**saved.json(), "stale": True, "cached_at": utc_iso(expired)}
        assert raw not in stale.text
        empty = restarted.get("/forex/eurjpy", headers={"X-Request-ID": "forex-proxy-test"})
        assert empty.status_code == 503
        assert empty.json() == {"detail": UPSTREAM_UNAVAILABLE, "status_code": 503}
        assert raw not in empty.text
        assert empty.headers["X-Request-ID"] == "forex-proxy-test"
    assert any(raw in record.getMessage() and getattr(record, "request_id", None) == "forex-proxy-test"
               for record in caplog.records)


def test_quote_stale_fallback_is_single_flight_and_returns_independent_data(monkeypatch, async_db):
    engine, sessions = async_db

    async def scenario():
        await database.init_db()
        cached_at = to_db() - timedelta(hours=2)
        async with sessions() as session:
            session.add(StockPrice(symbol="TEST", interval="quote", timestamp=cached_at, cached_at=cached_at, close=99))
            await session.commit()

        entered = asyncio.Event()
        release = asyncio.Event()

        async def fail(*args):
            entered.set()
            await release.wait()
            raise UpstreamUnavailableError()

        fake = AsyncMock(side_effect=fail)
        monkeypatch.setattr(stock_service, "fetch", fake)

        async def quote():
            async with sessions() as session:
                return await stock_service.get_current_price("test", session)

        tasks = [asyncio.create_task(quote()) for _ in range(10)]
        await entered.wait()
        release.set()
        results = await asyncio.gather(*tasks)
        fake.assert_awaited_once()
        assert all(row["stale"] is True and row["cached_at"] == utc_iso(cached_at) for row in results)
        results[0]["price"] = 0
        assert results[1]["price"] == 99
        await engine.dispose()

    asyncio.run(scenario())


def test_watchlist_summary_includes_oldest_stale_time(client, users, monkeypatch):
    user = users[0]
    path = f"/user/{user['id']}/lists/{user['list_id']}"
    assert client.post(f"{path}/items", headers=user["headers"], json={"symbol": "TEST"}).status_code == 200
    expired = "2026-01-01T10:00:00Z"
    monkeypatch.setattr(stock_service, "get_current_price", AsyncMock(
        return_value={"price": 99, "stale": True, "cached_at": expired},
    ))
    monkeypatch.setattr(profile_service, "get_profile", AsyncMock(return_value={"name": "Company"}))
    monkeypatch.setattr(indicator_service, "get_indicators", AsyncMock(
        return_value={"per": 15, "stale": True, "cached_at": "2026-01-01T11:00:00Z"},
    ))
    summary = client.get(f"{path}/summary", headers=user["headers"])
    assert summary.status_code == 200
    row = summary.json()["items"][0]
    assert row["stale"] is True and row["cached_at"] == expired
    assert row["price"] == 99 and row["per"] == 15 and row["error"] is None
