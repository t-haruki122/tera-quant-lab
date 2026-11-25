"""JWT発行とユーザー認証・所有者確認の依存関係。"""

from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.db_models import User

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


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    unauthorized = HTTPException(
        status_code=401,
        detail="有効な認証トークンが必要です",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    try:
        payload = jwt.decode(
            credentials.credentials,
            get_settings().secret_key,
            algorithms=["HS256"],
            options={"require": ["sub", "iat", "exp"]},
        )
        subject = payload["sub"]
        if not isinstance(subject, str) or not subject.isascii() or not subject.isdecimal():
            raise unauthorized
        user_id = int(subject)
        if not 0 < user_id <= 2**63 - 1:
            raise unauthorized
    except (jwt.InvalidTokenError, ValueError, TypeError, OverflowError):
        raise unauthorized from None
    user = await db.get(User, user_id)
    if user is None:
        raise unauthorized
    return user


async def require_user_owner(
    user_id: int, current_user: User = Depends(get_current_user)
) -> User:
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="他のユーザーのデータにはアクセスできません")
    return current_user
