"""Async CLI engine or a caller-supplied synchronous migration connection."""

import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.database import Base
from app.models import db_models  # noqa: F401

config = context.config
target_metadata = Base.metadata


def run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async():
    engine = create_async_engine(get_settings().database_url, poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("These data migrations require an online database connection")
elif config.attributes.get("connection") is not None:
    run_migrations(config.attributes["connection"])
else:
    # Only the standalone CLI takes this branch; app startup supplies a connection.
    asyncio.run(run_async())
