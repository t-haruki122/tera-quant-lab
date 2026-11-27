import asyncio
import importlib
import logging
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

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
            added = {
                "alembic_version", "api_stats_daily", "price_alerts", "backtest_runs", "symbol_directory", "api_tokens",
                "jq_issues", "jq_fin_summaries", "jq_sync_state", "fund_navs",
            }
            assert set(tables) == set(LegacyBase.metadata.tables) | added
            assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0012"
            columns = await connection.run_sync(lambda conn: inspect(conn).get_columns("users"))
            token_version = next(column for column in columns if column["name"] == "token_version")
            assert not token_version["nullable"]
        await engine.dispose()

    asyncio.run(scenario())


def test_symbol_directory_upgrade_downgrade(settings):
    engine = create_engine(settings.database_url.replace("+aiosqlite", ""))
    with engine.begin() as connection:
        upgrade(connection, "0007")
        connection.execute(text("INSERT INTO users (id, username, password_hash, created_at) "
                                "VALUES (1, 'keep', 'hash', CURRENT_TIMESTAMP)"))
        upgrade(connection, "0008")
        table = "symbol_directory"
        assert inspect(connection).get_pk_constraint(table)["constrained_columns"] == ["symbol"]
        connection.execute(text("INSERT INTO symbol_directory "
                                "VALUES ('130A.T', 'テスト', 'テスト', 'JP', NULL, NULL, 0, 'jpx', CURRENT_TIMESTAMP)"))
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.attributes["connection"] = connection
        command.downgrade(config, "0007")
        assert not inspect(connection).has_table(table)
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0007"
        assert connection.scalar(text("SELECT username FROM users WHERE id = 1")) == "keep"
    engine.dispose()


def test_fund_nav_upgrade_uniqueness_and_downgrade(settings):
    engine = create_engine(settings.database_url.replace("+aiosqlite", ""))
    with engine.begin() as connection:
        upgrade(connection, "0011")
        connection.execute(text("INSERT INTO users (id, username, password_hash, created_at) "
                                "VALUES (1, 'keep', 'hash', CURRENT_TIMESTAMP)"))
        assert not inspect(connection).has_table("fund_navs")
        upgrade(connection, "0012")
        assert inspect(connection).get_pk_constraint("fund_navs")["constrained_columns"] == ["association_code", "base_date"]
        insert = text("INSERT INTO fund_navs (association_code, base_date, nav, fund_name, fetched_at, stats) "
                      "VALUES ('0331418A', :day, 35610, 'オルカン', CURRENT_TIMESTAMP, '{}')")
        connection.execute(insert, {"day": "2026-11-27"})
        connection.execute(insert, {"day": "2026-11-30"})
        with pytest.raises(IntegrityError):
            connection.execute(insert, {"day": "2026-11-27"})
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.attributes["connection"] = connection
        command.downgrade(config, "0011")
        assert not inspect(connection).has_table("fund_navs")
        assert inspect(connection).has_table("jq_fin_summaries")
        assert connection.scalar(text("SELECT username FROM users WHERE id=1")) == "keep"
    engine.dispose()


def test_api_tokens_upgrade_downgrade_and_foreign_key(settings):
    engine = create_engine(settings.database_url.replace("+aiosqlite", ""))
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys = ON")
        upgrade(connection, "0008")
        connection.execute(text("INSERT INTO users (id, username, password_hash, created_at) "
                                "VALUES (1, 'keep', 'hash', CURRENT_TIMESTAMP), (2, 'remove', 'hash', CURRENT_TIMESTAMP)"))
        upgrade(connection, "0009")
        inspector = inspect(connection)
        assert inspector.get_foreign_keys("api_tokens")[0]["options"]["ondelete"] == "CASCADE"
        assert inspector.get_indexes("api_tokens")[0]["column_names"] == ["user_id"]
        assert inspector.get_unique_constraints("api_tokens")[0]["column_names"] == ["token_hash"]
        connection.execute(text("INSERT INTO api_tokens (user_id, name, token_hash, token_prefix, created_at) "
                                "VALUES (2, 'test', 'hash', 'tql_example1', CURRENT_TIMESTAMP)"))
        connection.execute(text("DELETE FROM users WHERE id=2"))
        assert connection.scalar(text("SELECT count(*) FROM api_tokens")) == 0
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.attributes["connection"] = connection
        command.downgrade(config, "0008")
        assert not inspect(connection).has_table("api_tokens")
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0008"
        assert connection.scalar(text("SELECT username FROM users WHERE id=1")) == "keep"
    engine.dispose()


def test_financial_currency_upgrade_and_downgrade_preserve_amounts(settings):
    engine = create_engine(settings.database_url.replace("+aiosqlite", ""))
    with engine.begin() as connection:
        upgrade(connection, "0009")
        connection.execute(text("INSERT INTO financials (symbol, revenue, net_income, cached_at) "
                                "VALUES ('TM', 300000000000, 205000000000, CURRENT_TIMESTAMP)"))
        amounts = text("SELECT symbol, revenue, net_income FROM financials")
        before = connection.execute(amounts).all()

        upgrade(connection, "0010")
        assert connection.execute(amounts).all() == before
        assert connection.scalar(text("SELECT currency FROM financials")) is None
        connection.execute(text("UPDATE financials SET currency = 'JPY'"))

        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.attributes["connection"] = connection
        command.downgrade(config, "0009")
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0009"
        assert "currency" not in {column["name"] for column in inspect(connection).get_columns("financials")}
        assert connection.execute(amounts).all() == before
    engine.dispose()


def test_jquants_upgrade_downgrade_indexes_and_existing_data(settings):
    engine = create_engine(settings.database_url.replace("+aiosqlite", ""))
    with engine.begin() as connection:
        upgrade(connection, "0010")
        connection.execute(text("INSERT INTO users (id, username, password_hash, created_at) "
                                "VALUES (1, 'keep', 'hash', CURRENT_TIMESTAMP)"))
        upgrade(connection, "0011")
        inspector = inspect(connection)
        tables = {"jq_issues", "jq_fin_summaries", "jq_sync_state"}
        assert tables <= set(inspector.get_table_names())
        assert inspector.get_pk_constraint("jq_fin_summaries")["constrained_columns"] == ["disc_no"]
        assert {tuple(index["column_names"]) for index in inspector.get_indexes("jq_fin_summaries")} == {
            ("code5", "period_end"), ("symbol", "disclosed_date"),
        }
        connection.execute(text("INSERT INTO jq_sync_state VALUES ('last_synced_date', '2026-09-04')"))
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.attributes["connection"] = connection
        command.downgrade(config, "0010")
        assert not tables & set(inspect(connection).get_table_names())
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0010"
        assert connection.scalar(text("SELECT username FROM users WHERE id=1")) == "keep"
    engine.dispose()


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


def test_upgrade_populated_0005_and_downgrade_0006(settings):
    engine = create_engine(settings.database_url.replace("+aiosqlite", ""))
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys = ON")
        upgrade(connection, "0005")
        connection.execute(text("INSERT INTO users (id, username, password_hash, created_at) "
                                "VALUES (1, 'keep', 'hash', CURRENT_TIMESTAMP), (2, 'remove', 'hash', CURRENT_TIMESTAMP)"))
        connection.execute(text("INSERT INTO portfolio_holdings (user_id, symbol, shares, avg_cost, created_at, updated_at) "
                                "VALUES (1, 'AAPL', 3, 100, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
        before = connection.execute(text("SELECT * FROM portfolio_holdings")).all()
        upgrade(connection, "0006")
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0006"
        assert inspect(connection).get_foreign_keys("backtest_runs")[0]["options"]["ondelete"] == "CASCADE"
        connection.execute(text("INSERT INTO backtest_runs (user_id, name, kind, request, summary, result, created_at) "
                                "VALUES (2, 'run', 'single', '{}', '{}', '{}', CURRENT_TIMESTAMP)"))
        connection.execute(text("DELETE FROM users WHERE id=2"))
        assert connection.scalar(text("SELECT COUNT(*) FROM backtest_runs")) == 0
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.attributes["connection"] = connection
        command.downgrade(config, "0005")
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0005"
        assert not inspect(connection).has_table("backtest_runs")
        assert connection.execute(text("SELECT * FROM portfolio_holdings")).all() == before
        assert connection.scalar(text("SELECT username FROM users WHERE id=1")) == "keep"
    engine.dispose()


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
            upgrade(connection, "0005")
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


def test_upgrade_populated_0006_moves_holdings_to_default_list_and_downgrades(settings):
    engine = create_engine(settings.database_url.replace("+aiosqlite", ""))
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys = ON")
        upgrade(connection, "0006")
        connection.execute(text("""
            INSERT INTO users (id, username, password_hash, created_at) VALUES
            (1, 'has-lists', 'hash', CURRENT_TIMESTAMP), (2, 'needs-list', 'hash', CURRENT_TIMESTAMP)
        """))
        connection.execute(text("""
            INSERT INTO user_lists (id, user_id, name, created_at, updated_at) VALUES
            (4, 1, 'newer', '2025-03-01', CURRENT_TIMESTAMP),
            (6, 1, 'default', '2025-02-01', CURRENT_TIMESTAMP),
            (8, 1, 'same-date', '2025-02-01', CURRENT_TIMESTAMP)
        """))
        connection.execute(text("""
            INSERT INTO user_list_items (list_id, symbol, tags, added_at) VALUES
            (6, 'aapl', '["keep"]', '2025-02-01'), (6, 'WATCH', NULL, NULL), (4, 'MSFT', NULL, NULL)
        """))
        connection.execute(text("""
            INSERT INTO portfolio_holdings (user_id, symbol, shares, avg_cost, created_at, updated_at) VALUES
            (1, 'AAPL', 1.2345, 240, '2025-02-03', CURRENT_TIMESTAMP),
            (1, 'MSFT', 5, 100, '2025-03-04', CURRENT_TIMESTAMP),
            (2, '7203.T', 12.123456, 1000, '2025-01-01', CURRENT_TIMESTAMP)
        """))
        connection.execute(text("""
            INSERT INTO price_alerts (user_id, symbol, kind, threshold, created_at)
            VALUES (1, 'AAPL', 'above', 200, CURRENT_TIMESTAMP)
        """))

        upgrade(connection)
        assert not inspect(connection).has_table("portfolio_holdings")
        assert connection.execute(text("SELECT name, cash_jpy FROM user_lists WHERE user_id=2")).one() == ("マイリスト", None)
        expected = [
            ("default", "AAPL", '["keep"]', "2025-02-01", 1.2345),
            ("default", "MSFT", None, "2025-03-04", 5),
            ("default", "WATCH", None, None, None),
            ("newer", "MSFT", None, None, None),
            ("マイリスト", "7203.T", None, "2025-01-01", 12.123456),
        ]
        columns = "name, symbol, tags, added_at"
        tables = "FROM user_lists JOIN user_list_items ON list_id=user_lists.id ORDER BY name, symbol"
        assert connection.execute(text(f"SELECT {columns}, shares {tables}")).all() == expected

        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.attributes["connection"] = connection
        command.downgrade(config, "0006")
        assert connection.scalar(text("SELECT count(*) FROM portfolio_holdings")) == 0
        assert connection.scalar(text("SELECT count(*) FROM price_alerts")) == 1
        for table, removed in [("user_lists", "cash_jpy"), ("user_list_items", "shares")]:
            assert removed not in {column["name"] for column in inspect(connection).get_columns(table)}
        assert connection.execute(text(f"SELECT {columns} {tables}")).all() == [row[:-1] for row in expected]
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    engine.dispose()
