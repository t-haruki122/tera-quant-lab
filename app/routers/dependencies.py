"""Dependencies shared by public data endpoints."""

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    bearer,
    find_authenticated_user,
    find_read_user,
    has_api_token,
    require_authenticated_user,
)
from app.config import get_settings
from app.database import get_db
from app.models.db_models import User
from app.rate_limit import limiter


async def limit_data_requests(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> None:
    if has_api_token(credentials):
        require_authenticated_user(None)
    settings = get_settings()
    if settings.rate_limit_enabled:
        user = await find_authenticated_user(credentials, db)
        limiter.data(request, settings, user_id=user.id if user else None)


async def limit_read_data_requests(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> None:
    settings = get_settings()
    if settings.rate_limit_enabled or has_api_token(credentials):
        user = await find_read_user(credentials, db)
        limiter.data(request, settings, user_id=user.id if user else None)


async def validate_optional_api_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> None:
    if has_api_token(credentials):
        await find_read_user(credentials, db)


async def limit_read_backtest_requests(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    user = await find_read_user(credentials, db)
    limiter.data(request, get_settings(), user_id=user.id if user else None)
    return require_authenticated_user(user)


async def limit_backtest_requests(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    user = await find_authenticated_user(credentials, db)
    limiter.data(request, get_settings(), user_id=user.id if user else None)
    return require_authenticated_user(user)
