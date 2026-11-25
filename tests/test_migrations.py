import asyncio
import importlib
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app import database
from app.timeutil import to_db
from migrations.legacy_schema import Base as LegacyBase


def upgrade(connection, target="head"):
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["connection"] = connection
    command.upgrade(config, target)


def test_fresh_startup_and_second_upgrade_are_idempotent(async_db):
    engine, _ = async_db

    async def scenario():
        logger = logging.getLogger("app.main")
        await database.init_db()
        await database.init_db()
        assert not logger.disabled
        async with engine.connect() as connection:
            tables = await connection.run_sync(lambda conn: inspect(conn).get_table_names())
            assert set(tables) == set(LegacyBase.metadata.tables) | {"alembic_version", "api_stats_daily"}
            assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0003"
        await engine.dispose()

    asyncio.run(scenario())


def test_upgrade_legacy_duplicates_normalization_constraints_and_user_data(settings):
    engine = create_engine(settings.database_url.replace("+aiosqlite", ""))
    now = to_db()
    with engine.begin() as connection:
        LegacyBase.metadata.create_all(connection)
        connection.execute(text("DROP INDEX ix_stock_prices_symbol"))
        for table in ("stock_prices", "financials", "news", "company_profiles", "indicators"):
            extras = {
                "stock_prices": {"timestamp": now, "close": 1, "open": 1},
                "financials": {"revenue": 1},
                "news": {"title": "first", "url": "https://example.invalid/article"},
                "company_profiles": {"name": "first"},
                "indicators": {"per": 1},
            }[table]
            # Highest id must lose to a newer cached_at, while equal times use id.
            for identity, symbol, age in [(1, "aapl", 1), (2, "AAPL", 0), (3, "AaPl", 0), (4, "aapl", 1)]:
                from datetime import timedelta

                connection.execute(LegacyBase.metadata.tables[table].insert().values(
                    id=identity, symbol=symbol, cached_at=now - timedelta(days=age), **extras))
        user_tables = {
            "users": dict(id=1, username="untouched", password_hash="hash", created_at=now),
            "user_lists": dict(id=1, user_id=1, name="list", created_at=now, updated_at=now),
            "user_list_items": dict(id=1, list_id=1, symbol="lower", tags='["tag"]'),
            "stock_memos": dict(id=1, user_id=1, symbol="lower", memo="memo", updated_at=now),
        }
        for table, row in user_tables.items():
            connection.execute(LegacyBase.metadata.tables[table].insert().values(**row))
        before = {table: connection.execute(text(f"SELECT * FROM {table}")).all() for table in user_tables}
        upgrade(connection)
        assert any(index["name"] == "ix_stock_prices_symbol" for index in inspect(connection).get_indexes("stock_prices"))
        revision = importlib.import_module("migrations.versions.0002_cache_integrity")
        for table, keys in revision.KEYS.items():
            assert connection.execute(text(f"SELECT id, symbol FROM {table}")).all() == [(3, "AAPL")]
            constraints = inspect(connection).get_unique_constraints(table)
            assert any(item["column_names"] == keys for item in constraints)
        after = {table: connection.execute(text(f"SELECT * FROM {table}")).all() for table in user_tables}
        assert before == after
        snapshot = connection.connection.driver_connection.iterdump()
        snapshot = list(snapshot)
        upgrade(connection)
        assert list(connection.connection.driver_connection.iterdump()) == snapshot
        # Dropping constraints must work and preserve surviving data.
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.attributes["connection"] = connection
        command.downgrade(config, "0001")
        assert not inspect(connection).get_unique_constraints("stock_prices")
        assert connection.scalar(text("SELECT count(*) FROM users")) == 1
        upgrade(connection)
    engine.dispose()


def test_upgrade_0002_with_data_and_downgrade_0003(settings):
    engine = create_engine(settings.database_url.replace("+aiosqlite", ""))
    with engine.begin() as connection:
        upgrade(connection, "0002")
        connection.execute(LegacyBase.metadata.tables["users"].insert().values(
            username="preserved", password_hash="hash", created_at=to_db(),
        ))
        before = connection.execute(text("SELECT * FROM users")).all()
        upgrade(connection)
        inspector = inspect(connection)
        assert inspector.get_pk_constraint("api_stats_daily")["constrained_columns"] == ["date", "endpoint"]
        assert connection.execute(text("SELECT * FROM users")).all() == before
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.attributes["connection"] = connection
        command.downgrade(config, "0002")
        assert "api_stats_daily" not in inspect(connection).get_table_names()
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0002"
        assert connection.execute(text("SELECT * FROM users")).all() == before
    engine.dispose()
