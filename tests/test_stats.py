import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import database, main
from app import stats as stats_module
from app.models.db_models import ApiStatsDaily
from app.routers import stats as stats_router
from app.services import stock_service
from app.stats import AppStats

OLD_FIELDS = {
    "api_calls", "cache_hits", "total_requests", "hit_rate_percent", "server_requests", "server_errors",
    "error_rate_percent", "avg_response_time_ms", "top_endpoints", "uptime_seconds",
}
NOW = datetime(2026, 11, 26, 23, 50, tzinfo=UTC)


@pytest.fixture
def fresh_stats(monkeypatch):
    monkeypatch.setattr(stats_module, "utcnow", lambda: NOW)
    instance = AppStats()
    monkeypatch.setattr(main, "app_stats", instance)
    monkeypatch.setattr(stats_router, "stats", instance)
    return instance


def test_route_templates_and_live_response_fields(fresh_stats, client, monkeypatch):
    monkeypatch.setattr(stock_service, "get_history", AsyncMock(return_value={"symbol": "X", "history": []}))
    initial = client.get("/stats").json()
    assert set(initial) == OLD_FIELDS | {"today", "last_7_days", "cache_hit_rate"}
    assert initial["cache_hit_rate"] is None and initial["hit_rate_percent"] == 0
    assert initial["today"] == {"date": "2026-11-26", "hits": 0, "errors": 0, "total_ms": 0}
    assert client.get("/stock/AAPL/history").status_code == 200
    assert client.get("/stock/7203.T/history").status_code == 200
    assert client.get("/user/1/lists/25").status_code == 401
    assert client.get("/unknown/112").status_code == 404
    assert client.get("/").status_code == 200
    assert client.get("/health").status_code == 200

    fresh_stats.log_cache_hit()
    fresh_stats.log_cache_hit()
    fresh_stats.log_api_call()
    current = client.get("/stats").json()
    assert current["server_requests"] == current["today"]["hits"] == 5
    assert current["server_errors"] == current["today"]["errors"] == 2
    assert current["total_requests"] == 3
    assert current["hit_rate_percent"] == 66.7 and current["cache_hit_rate"] == 2 / 3
    assert current["today"]["total_ms"] > 0
    assert len(current["last_7_days"]) == 7 and current["last_7_days"][-1] == current["today"]
    assert current["last_7_days"][0]["date"] == "2026-11-20"
    assert {row["endpoint"]: row["count"] for row in current["top_endpoints"]} == {
        "/stock/{symbol}/history": 2, "/user/{user_id}/lists/{list_id}": 1, "unmatched": 1, "/stats": 1,
    }


def test_static_mount_is_excluded_but_unmatched_and_stock_routes_are_counted(fresh_stats, client, monkeypatch):
    monkeypatch.setattr(stock_service, "get_history", AsyncMock(return_value={"symbol": "AAPL", "history": []}))

    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/index.css").status_code == 200
    assert client.get("/static/nonexistent.js").status_code == 404
    assert client.get("/nonexistent").status_code == 404
    assert client.get("/static-unmatched").status_code == 404
    assert client.get("/stock/AAPL/history").status_code == 200

    result = client.get("/stats").json()
    assert result["server_requests"] == result["today"]["hits"] == 3
    assert result["server_errors"] == result["today"]["errors"] == 2
    assert {row["endpoint"]: row["count"] for row in result["top_endpoints"]} == {
        "unmatched": 2, "/stock/{symbol}/history": 1,
    }


def test_flush_upserts_and_mixes_database_and_pending_through_failure(fresh_stats, async_db, monkeypatch):
    engine, sessions = async_db

    async def scenario():
        await database.init_db()
        async with sessions.begin() as db:
            db.add_all([
                ApiStatsDaily(date=NOW.date() - timedelta(days=91), endpoint="/old", hits=1, errors=0, total_ms=3),
                ApiStatsDaily(date=NOW.date() - timedelta(days=1), endpoint="/earlier", hits=2, errors=0, total_ms=30),
            ])
        fresh_stats.log_request("/stock/{symbol}", .01, 200)
        assert await fresh_stats.flush()
        fresh_stats.log_request("/stock/{symbol}", .02, 501)
        fresh_stats.log_request("/stats", .03, 200)

        class FailingCommit(AsyncSession):
            async def commit(self):
                # A request completed during the failed write. This new delta
                # and the previously captured batch must both survive.
                fresh_stats.log_request("/stock/{symbol}", .04, 200)
                raise RuntimeError("database temporarily unavailable")

        with monkeypatch.context() as patch:
            patch.setattr(database, "AsyncSessionLocal", async_sessionmaker(engine, class_=FailingCommit))
            assert not await fresh_stats.flush()
        combined = await fresh_stats.daily_stats()
        assert combined["last_7_days"][-2] == {"date": "2026-11-25", "hits": 2, "errors": 0, "total_ms": 30}
        assert combined["today"] == {"date": "2026-11-26", "hits": 4, "errors": 1, "total_ms": 100}
        assert await fresh_stats.flush()
        assert await fresh_stats.flush()
        async with sessions() as db:
            rows = (await db.scalars(select(ApiStatsDaily).order_by(ApiStatsDaily.date, ApiStatsDaily.endpoint))).all()
            assert [(row.endpoint, row.hits, row.errors, row.total_ms) for row in rows] == [
                ("/earlier", 2, 0, 30), ("/stats", 1, 0, 30), ("/stock/{symbol}", 3, 1, 70),
            ]
        assert await AppStats().daily_stats() == combined
        await engine.dispose()

    asyncio.run(scenario())


def test_lifespan_flushes_periodically_and_on_shutdown(fresh_stats, async_db, settings, monkeypatch):
    engine, sessions = async_db
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(settings, "stats_flush_seconds", .01)
    stopped = asyncio.Event()
    original_flush = fresh_stats.flush

    async def signal_flush():
        result = await original_flush()
        stopped.set()
        return result

    monkeypatch.setattr(fresh_stats, "flush", signal_flush)

    async def scenario():
        async with main.lifespan(main.app):
            fresh_stats.log_request("/periodic", .01, 200)
            await asyncio.wait_for(stopped.wait(), timeout=5)
            fresh_stats.log_request("/shutdown", .01, 200)
        async with sessions() as db:
            rows = (await db.scalars(select(ApiStatsDaily).order_by(ApiStatsDaily.endpoint))).all()
            assert [(row.endpoint, row.hits) for row in rows] == [("/periodic", 1), ("/shutdown", 1)]
        await engine.dispose()

    asyncio.run(scenario())
