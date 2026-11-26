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
            added = {"alembic_version", "api_stats_daily", "portfolio_holdings", "price_alerts"}
            assert set(tables) == set(LegacyBase.metadata.tables) | added
            assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0005"
            columns = await connection.run_sync(lambda conn: inspect(conn).get_columns("users"))
            token_version = next(column for column in columns if column["name"] == "token_version")
            assert not token_version["nullable"]
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
        upgrade(connection, "0003")
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
        upgrade(connection, "0003")
        assert list(connection.connection.driver_connection.iterdump()) == snapshot
        # Dropping constraints must work and preserve surviving data.
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.attributes["connection"] = connection
        command.downgrade(config, "0001")
        assert not inspect(connection).get_unique_constraints("stock_prices")
        assert connection.scalar(text("SELECT count(*) FROM users")) == 1
        upgrade(connection, "0003")
    engine.dispose()


def test_upgrade_0002_with_data_and_downgrade_0003(settings):
    engine = create_engine(settings.database_url.replace("+aiosqlite", ""))
    with engine.begin() as connection:
        upgrade(connection, "0002")
        connection.execute(LegacyBase.metadata.tables["users"].insert().values(
            username="preserved", password_hash="hash", created_at=to_db(),
        ))
        before = connection.execute(text("SELECT * FROM users")).all()
        upgrade(connection, "0003")
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


def test_upgrade_0003_and_downgrade_0004_keep_users_and_watchlists(settings):
    engine = create_engine(settings.database_url.replace("+aiosqlite", ""))
    now = to_db()
    with engine.begin() as connection:
        upgrade(connection, "0003")
        rows = {
            "users": dict(id=10, username="existing", password_hash="hash", created_at=now),
            "user_lists": dict(id=20, user_id=10, name="保有銘柄", created_at=now, updated_at=now),
            "user_list_items": dict(id=30, list_id=20, symbol="AAPL", tags='["長期"]'),
            "stock_memos": dict(id=40, user_id=10, symbol="AAPL", memo="preserved", updated_at=now),
        }
        for name, row in rows.items():
            connection.execute(LegacyBase.metadata.tables[name].insert().values(**row))

        def snapshot():
            return {name: connection.execute(select_legacy_columns(name)).all() for name in rows}

        before = snapshot()
        upgrade(connection, "0004")
        assert connection.scalar(text("SELECT token_version FROM users WHERE id=10")) == 0
        assert connection.scalar(text("SELECT added_at FROM user_list_items WHERE id=30")) is None
        assert snapshot() == before
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        assert connection.execute(text("SELECT seq FROM sqlite_sequence WHERE name='users'")).scalar() == 10
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.attributes["connection"] = connection
        command.downgrade(config, "0003")
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0003"
        for table, removed in (("users", "token_version"), ("user_list_items", "added_at")):
            assert removed not in {column["name"] for column in inspect(connection).get_columns(table)}
        assert snapshot() == before
    engine.dispose()


def select_legacy_columns(table):
    return LegacyBase.metadata.tables[table].select()


def test_upgrade_populated_0004_and_downgrade_0005(settings):
    engine = create_engine(settings.database_url.replace("+aiosqlite", ""))
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys = ON")
        connection.commit()
        with connection.begin():
            upgrade(connection, "0004")
            now = to_db()
            old_rows = {
                "users": dict(id=1, username="existing", password_hash="hash", created_at=now),
                "user_lists": dict(id=1, user_id=1, name="監視", created_at=now, updated_at=now),
                "user_list_items": dict(id=1, list_id=1, symbol="AAPL", tags='["長期"]'),
                "stock_memos": dict(id=1, user_id=1, symbol="AAPL", memo="メモ", updated_at=now),
                "stock_prices": dict(id=1, symbol="AAPL", timestamp=now, close=150, cached_at=now),
            }
            for table, row in old_rows.items():
                connection.execute(LegacyBase.metadata.tables[table].insert().values(**row))
            connection.execute(text("UPDATE stock_prices SET interval = 'quote' WHERE id = 1"))
            inspector = inspect(connection)
            columns = {
                table: ", ".join(column["name"] for column in inspector.get_columns(table))
                for table in old_rows
            }

            def snapshot():
                return {name: connection.execute(text(f"SELECT {names} FROM {name}")).all()
                        for name, names in columns.items()}

            before = snapshot()
            upgrade(connection)
            assert snapshot() == before
            assert connection.scalar(text("SELECT quote_currency FROM stock_prices")) is None
            assert connection.scalar(text("SELECT quote_market_time FROM stock_prices")) is None
            assert connection.scalar(text("SELECT quote_market_date FROM stock_prices")) is None
            connection.execute(text(
                "INSERT INTO portfolio_holdings (user_id, symbol, shares, avg_cost, created_at, updated_at) "
                "VALUES (1, 'AAPL', 1, 100, :now, :now)"
            ), {"now": now})
            connection.execute(text(
                "INSERT INTO price_alerts (user_id, symbol, kind, threshold, created_at) "
                "VALUES (1, 'AAPL', 'above', 150, :now)"
            ), {"now": now})
            assert connection.scalar(text("SELECT last_triggered_market_date FROM price_alerts")) is None
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
            for table in ("portfolio_holdings", "price_alerts"):
                assert inspect(connection).get_foreign_keys(table)[0]["options"]["ondelete"] == "CASCADE"

            config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
            config.attributes["connection"] = connection
            command.downgrade(config, "0004")
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0004"
            assert not {"portfolio_holdings", "price_alerts"} & set(inspect(connection).get_table_names())
            removed = {"quote_currency", "quote_market_time", "quote_market_date"}
            assert not removed & {column["name"] for column in inspect(connection).get_columns("stock_prices")}
            assert snapshot() == before
    engine.dispose()
