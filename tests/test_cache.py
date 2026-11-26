import asyncio
import importlib
import threading
from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import func, select

from app import cache, database
from app.exceptions import UpstreamUnavailableError
from app.models.db_models import CompanyProfile, Financial, Indicator, News, StockPrice, User
from app.services import finance_service
from app.singleflight import flight
from app.timeutil import to_db, utc_iso


@pytest.mark.parametrize("module,service,method,data,model", [
    ("stock", "get_current_price", "get_current_price", {"symbol": "AAPL", "price": 123,
     "timestamp": "2026-01-01T12:34:56.123456Z"}, StockPrice),
    ("stock", "get_history", "get_history", [{"date": "2026-01-01", "open": 1, "high": 3,
     "low": 1, "close": 2, "volume": 10}], StockPrice),
    ("finance", "get_financials", "get_financials", {"symbol": "AAPL", "revenue": 1}, Financial),
    ("finance", "get_financial_history", "get_financial_history", {"symbol": "AAPL", "history": []}, None),
    ("finance", "get_dividend_history", "get_dividend_history", {"symbol": "AAPL", "history": []}, None),
    ("profile", "get_profile", "get_company_profile", {"symbol": "AAPL", "name": "Apple"}, CompanyProfile),
    ("indicator", "get_indicators", "get_indicators", {"symbol": "AAPL", "per": 1}, Indicator),
    ("news", "get_news", "get_news", [{"title": "title", "url": "https://example.invalid/a"}], News),
    ("forex", "get_exchange_rate", "get_exchange_rate", {"pair": "AAPL", "rate": 1,
     "timestamp": utc_iso()}, StockPrice),
])
def test_ten_concurrent_cold_service_calls_share_one_client_call(
    monkeypatch, async_db, module, service, method, data, model,
):
    engine, sessions = async_db
    module = importlib.import_module(f"app.services.{module}_service")
    def external(*args, **kwargs):
        assert args[0] == "AAPL"
        return data

    fake = Mock(side_effect=external)
    client = module._news_client if service == "get_news" else module._client
    monkeypatch.setattr(client, method, fake)
    if service == "get_news":
        monkeypatch.setattr(module, "get_profile", AsyncMock(return_value={"name": "Apple"}))

    async def scenario():
        await database.init_db()

        async def request():
            async with sessions() as session:
                return await getattr(module, service)("aapl", session)

        results = await asyncio.gather(*(request() for _ in range(10)))
        assert all(result == results[0] for result in results)
        fake.assert_called_once()
        if model:
            async with sessions() as session:
                assert await session.scalar(select(func.count()).select_from(model)) == 1
            # A sequential call should now use the database cache.
            await request()
            fake.assert_called_once()
        await engine.dispose()

    asyncio.run(scenario())


def test_upserts_refresh_and_retention_never_purges_user_tables(monkeypatch, async_db, settings):
    engine, sessions = async_db
    monkeypatch.setattr(settings, "cache_retention_days", 30)
    monkeypatch.setattr(settings, "cache_ttl_financials", 90 * 86400)
    monkeypatch.setattr(settings, "cache_stale_max_days", 7)
    offline = Mock(side_effect=UpstreamUnavailableError())
    monkeypatch.setattr(finance_service._client, "get_financials", offline)

    async def scenario():
        await database.init_db()
        now = to_db()
        fresh_financial = now - timedelta(days=89)
        stale_financial = now - timedelta(days=96)
        old = now - timedelta(days=98)
        async with sessions() as session:
            session.add(User(username="keep", password_hash="hash", created_at=old))
            session.add_all([
                StockPrice(symbol="old", timestamp=old, close=1, cached_at=old),
                Financial(symbol="old", cached_at=old),
                Financial(symbol="fresh", cached_at=fresh_financial),
                Financial(symbol="stale", cached_at=stale_financial),
                News(symbol="old", title="old", url="old", cached_at=old),
                News(symbol="retained", title="retained", url="retained", cached_at=now - timedelta(days=29)),
                CompanyProfile(symbol="old", name="old", cached_at=old),
                Indicator(symbol="old", cached_at=old),
                Indicator(symbol="fresh", cached_at=now),
            ])
            await session.commit()
            for model in cache.CACHE_KEYS:
                assert await session.scalar(select(func.count()).select_from(model)) >= 1
            # Startup always purges, using exactly the application startup path.
        await database.init_db()
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(User)) == 1
            for model in cache.CACHE_KEYS:
                expected = {Indicator: 1, Financial: 2, News: 1}.get(model, 0)
                assert await session.scalar(select(func.count()).select_from(model)) == expected
            assert set(await session.scalars(select(Financial.symbol))) == {"FRESH", "STALE"}
            assert "stale" not in await finance_service.get_financials("FRESH", session)
            stale = await finance_service.get_financials("STALE", session)
            assert stale["stale"] is True and stale["cached_at"] == utc_iso(stale_financial)
            offline.assert_called_once_with("STALE")
            await cache.upsert_cache(session, CompanyProfile, {"symbol": "aapl", "name": "first"})
            await session.commit()
            profile = await session.scalar(select(CompanyProfile))
            first_time = profile.cached_at
            await cache.upsert_cache(session, CompanyProfile, {"symbol": "AAPL", "name": "second"})
            await session.commit()
            await session.refresh(profile)
            assert profile.symbol == "AAPL" and profile.name == "second"
            assert profile.cached_at >= first_time
            assert await session.scalar(select(func.count()).select_from(CompanyProfile)) == 1
            # Within an hour, opportunistic writes must skip the purge.
            execute = Mock(wraps=session.execute)
            monkeypatch.setattr(session, "execute", execute)
            await cache.purge_cache(session)
            execute.assert_not_called()
            key = str(session.bind.url)
            monkeypatch.setitem(cache._last_purge, key, cache._last_purge[key] - 3601)
            await cache.purge_cache(session)
            assert execute.call_count == len(cache.CACHE_KEYS)
            await session.commit()
        await engine.dispose()

    asyncio.run(scenario())


def test_quote_and_daily_history_at_same_time_remain_separate(async_db):
    engine, sessions = async_db

    async def scenario():
        await database.init_db()
        async with sessions() as session:
            timestamp = to_db().replace(hour=0, minute=0, second=0, microsecond=0)
            for interval, close in [("quote", 1), ("1d", 2), ("1d", 3)]:
                await cache.upsert_cache(session, StockPrice, {
                    "symbol": "aapl", "timestamp": timestamp, "interval": interval, "close": close,
                })
            await session.commit()
            rows = (await session.execute(select(StockPrice.interval, StockPrice.close))).all()
            assert sorted(rows) == [("1d", 3), ("quote", 1)]
        await engine.dispose()

    asyncio.run(scenario())


def test_cancelled_request_closes_its_session_without_cancelling_shared_cache_write(monkeypatch, async_db):
    from app.services import stock_service

    engine, sessions = async_db
    entered = threading.Event()
    release = threading.Event()

    def external(symbol):
        entered.set()
        assert release.wait(10)
        return {"symbol": symbol, "price": 1, "timestamp": utc_iso()}

    fake = Mock(side_effect=external)
    monkeypatch.setattr(stock_service._client, "get_current_price", fake)

    async def scenario():
        await database.init_db()

        async def request():
            async with sessions() as session:
                return await stock_service.get_current_price("aapl", session)

        first = asyncio.create_task(request())
        second = asyncio.create_task(request())
        assert await asyncio.to_thread(entered.wait, 10)
        first.cancel()
        assert isinstance((await asyncio.gather(first, return_exceptions=True))[0], asyncio.CancelledError)
        release.set()
        assert (await second)["price"] == 1
        fake.assert_called_once()
        assert flight.pending == 0
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(StockPrice)) == 1
        await engine.dispose()

    asyncio.run(scenario())


def test_service_failure_is_shared_and_all_parameters_are_part_of_key(monkeypatch, async_db):
    from app.services import finance_service, stock_service

    engine, sessions = async_db
    failure = RuntimeError("upstream failure")
    fake = Mock(side_effect=failure)
    monkeypatch.setattr(stock_service._client, "get_current_price", fake)
    history = Mock(side_effect=lambda symbol, limit: {"symbol": symbol, "history": [limit]})
    monkeypatch.setattr(finance_service._client, "get_financial_history", history)

    async def scenario():
        await database.init_db()

        async def request():
            async with sessions() as session:
                return await stock_service.get_current_price("aapl", session)

        results = await asyncio.gather(*(request() for _ in range(10)), return_exceptions=True)
        assert all(result is failure for result in results)
        fake.assert_called_once()
        assert flight.pending == 0

        async def financial(limit):
            async with sessions() as session:
                return await finance_service.get_financial_history("aapl", session, limit=limit)

        results = await asyncio.gather(*(financial(limit) for limit in [6] * 10 + [8] * 10))
        assert [result["history"] for result in results] == [[6]] * 10 + [[8]] * 10
        assert history.call_count == 2
        assert flight.pending == 0
        await engine.dispose()

    asyncio.run(scenario())
