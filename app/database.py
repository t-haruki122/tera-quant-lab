"""データベース接続・セッション管理"""

import importlib
from asyncio import timeout
from collections.abc import AsyncIterator
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=False,
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {},
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """SQLAlchemy ベースモデル"""
    pass


def ensure_sqlite_directory(url: URL) -> None:
    if url.get_backend_name() == "sqlite" and (database := url.database) and database != ":memory:":
        # Let SQLite interpret explicit file: URIs and their query options.
        if not (database.startswith("file:") and url.query.get("uri") == "true"):
            Path(database).parent.mkdir(parents=True, exist_ok=True)


async def check_health() -> str | None:
    async with timeout(1), engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
        if await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table("alembic_version")):
            return await conn.scalar(text("SELECT version_num FROM alembic_version"))
        return None


async def upgrade_db() -> None:
    """設定済みのデータベースにマイグレーションを適用します。"""
    # Register models even when called without importing the routers.
    importlib.import_module("app.models.db_models")

    def upgrade(connection):
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.attributes["connection"] = connection
        command.upgrade(config, "head")

    ensure_sqlite_directory(engine.url)
    async with engine.begin() as conn:
        await conn.run_sync(upgrade)


async def init_db():
    """マイグレーション適用と期限切れキャッシュの削除。"""
    await upgrade_db()

    from app.cache import purge_cache

    async with AsyncSessionLocal() as session:
        await purge_cache(session, force=True)
        await session.commit()


async def get_db() -> AsyncIterator[AsyncSession]:
    """DBセッションの依存性注入用ジェネレータ"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
