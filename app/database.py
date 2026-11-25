"""データベース接続・セッション管理"""

import importlib
from pathlib import Path

from alembic import command
from alembic.config import Config
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


async def init_db():
    """マイグレーション適用と期限切れキャッシュの削除。"""
    # Register models even when init_db is called without importing the routers.
    importlib.import_module("app.models.db_models")

    def upgrade(connection):
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.attributes["connection"] = connection
        command.upgrade(config, "head")

    async with engine.begin() as conn:
        await conn.run_sync(upgrade)

    from app.cache import purge_cache

    async with AsyncSessionLocal() as session:
        await purge_cache(session, force=True)
        await session.commit()


async def get_db() -> AsyncSession:
    """DBセッションの依存性注入用ジェネレータ"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
