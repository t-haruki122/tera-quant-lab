import asyncio
import logging
from unittest.mock import AsyncMock, Mock

import pytest

from app import database, main
from app.config import Settings
from app.exceptions import RateLimitError
from app.models.db_models import User, UserList, UserListItem
from app.services import cache_warmer, forex_service, stock_service


async def seed_watchlists(sessions, *watchlists):
    async with sessions() as db:
        for index, symbols in enumerate(watchlists):
            user = User(username=f"user{index}", password_hash="hash")
            db.add(user)
            await db.flush()
            watchlist = UserList(name="List", user_id=user.id)
            db.add(watchlist)
            await db.flush()
            db.add_all([UserListItem(symbol=symbol, list_id=watchlist.id) for symbol in symbols])
        await db.commit()


def test_warmer_deduplicates_symbols_caps_concurrency_and_backs_off(monkeypatch, async_db, caplog):
    engine, sessions = async_db

    async def scenario():
        await database.init_db()
        await seed_watchlists(sessions, ["AAA", "BBB", "DDD"], ["AAA", "CCC"])
        active = peak = 0
        started = []
        limit = False

        async def refresh(symbol, db=None):
            nonlocal active, peak
            started.append(symbol)
            active += 1
            peak = max(active, peak)
            try:
                await asyncio.sleep(0)
                if symbol == "AAA":
                    if limit:
                        raise RateLimitError()
                    raise RuntimeError("unexpected")
            finally:
                active -= 1

        monkeypatch.setattr(cache_warmer, "refresh_quote", refresh)
        monkeypatch.setattr(cache_warmer, "refresh_exchange_rate", refresh)
        await cache_warmer.warm_cache_once(2)
        assert started == ["AAA", "BBB", "CCC", "DDD", "USDJPY=X"]
        assert peak == 2

        started.clear()
        limit = True
        await cache_warmer.warm_cache_once(2)
        assert started == ["AAA", "BBB"]
        await engine.dispose()

    with caplog.at_level(logging.INFO, logger=cache_warmer.__name__):
        asyncio.run(scenario())
    summaries = [record.message for record in caplog.records if record.name == cache_warmer.__name__]
    assert summaries == [
        "Cache warming cycle: refreshed=4 failed=1 rate_limited=0 errors=RuntimeError",
        "Cache warming cycle: refreshed=1 failed=1 rate_limited=1 errors=RateLimitError",
    ]


def test_warmer_populates_the_same_quote_and_forex_caches_used_by_requests(monkeypatch, async_db):
    engine, sessions = async_db
    stamp = "2026-01-01T10:00:00Z"
    quote = Mock(return_value={"symbol": "AAA", "price": 15, "timestamp": stamp})
    forex = Mock(return_value={"pair": "USDJPY=X", "rate": 150, "timestamp": stamp})
    monkeypatch.setattr(stock_service._client, "get_current_price", quote)
    monkeypatch.setattr(forex_service._client, "get_exchange_rate", forex)

    async def scenario():
        await database.init_db()
        await seed_watchlists(sessions, ["AAA"])
        await cache_warmer.warm_cache_once(2)
        async with sessions() as db:
            assert await stock_service.get_current_price("AAA", db) == quote.return_value
            assert await forex_service.get_exchange_rate("USDJPY=X", db) == forex.return_value
        quote.assert_called_once_with("AAA")
        forex.assert_called_once_with("USDJPY=X")
        await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("enabled", [False, True])
def test_lifespan_starts_and_cancels_warmer_only_when_enabled(
    enabled, monkeypatch, async_db, settings,
):
    monkeypatch.delenv("CACHE_WARM_ENABLED")
    assert Settings(_env_file=None).cache_warm_enabled is False
    settings.cache_warm_enabled = enabled
    settings.cache_warm_interval_minutes = 11
    settings.cache_warm_concurrency = 3
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main.app_stats, "flush", AsyncMock())

    async def scenario():
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def warmer(interval, concurrency):
            assert (interval, concurrency) == (11, 3)
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        monkeypatch.setattr(main, "warm_cache_periodically", warmer)
        async with main.lifespan(main.app):
            if enabled:
                await asyncio.wait_for(started.wait(), timeout=2)
            else:
                await asyncio.sleep(0)
                assert not started.is_set()
        assert stopped.is_set() == enabled

    asyncio.run(scenario())


def test_cancelling_active_cycle_drains_inflight_refresh_and_skips_remaining_work(monkeypatch, async_db):
    engine, sessions = async_db

    async def scenario():
        await database.init_db()
        await seed_watchlists(sessions, ["AAA", "BBB"])
        started = asyncio.Event()
        finish = asyncio.Event()
        seen = []

        async def refresh(symbol, db=None):
            seen.append(symbol)
            started.set()
            await finish.wait()

        monkeypatch.setattr(cache_warmer, "refresh_quote", refresh)
        monkeypatch.setattr(cache_warmer, "refresh_exchange_rate", refresh)
        task = asyncio.create_task(cache_warmer.warm_cache_periodically(30, 1))
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert seen == ["AAA"]
        await engine.dispose()

    asyncio.run(scenario())
