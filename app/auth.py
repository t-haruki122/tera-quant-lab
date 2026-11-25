"""JWT発行とユーザー認証・所有者確認の依存関係。"""

from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.db_models import User
from app.validation import SQLiteID

bearer = HTTPBearer(auto_error=False)


def create_access_token(user_id: int) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "iat": now,
            "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
        },
        settings.secret_key,
        algorithm="HS256",
    )


async def find_authenticated_user(
    credentials: HTTPAuthorizationCredentials | None, db: AsyncSession,
) -> User | None:
    if credentials is None:
        return None
    try:
        payload = jwt.decode(
            credentials.credentials,
            get_settings().secret_key,
            algorithms=["HS256"],
            options={"require": ["sub", "iat", "exp"]},
        )
        subject = payload["sub"]
        if not isinstance(subject, str) or not subject.isascii() or not subject.isdecimal():
            return None
        user_id = int(subject)
        if not 0 < user_id <= 2**63 - 1:
            return None
    except (jwt.InvalidTokenError, ValueError, TypeError, OverflowError):
        return None
    return await db.get(User, user_id)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    return require_authenticated_user(await find_authenticated_user(credentials, db))


def require_authenticated_user(user: User | None) -> User:
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="有効な認証トークンが必要です",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def require_user_owner(
    user_id: SQLiteID, current_user: User = Depends(get_current_user)
) -> User:
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="他のユーザーのデータにはアクセスできません")
    return current_user
