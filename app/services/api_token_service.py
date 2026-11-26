"""Create and validate revocable personal API credentials."""

import hashlib
import re
import secrets
from datetime import datetime, timedelta

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import APIToken, User
from app.timeutil import to_db

TOKEN_PREFIX = "tql_"
TOKEN_PATTERN = re.compile(r"tql_[A-Za-z0-9_-]{43}")
MAX_ACTIVE_TOKENS = 10


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _active_at(now: datetime):
    return or_(APIToken.expires_at.is_(None), APIToken.expires_at > now)


async def list_tokens(user_id: int, db: AsyncSession):
    return (await db.scalars(select(APIToken).where(APIToken.user_id == user_id)
                             .order_by(APIToken.created_at.desc(), APIToken.id.desc()))).all()


async def create_token(
    user_id: int, name: str, expires_in_days: int | None, db: AsyncSession,
) -> tuple[APIToken, str] | None:
    from app.services.user_service import lock_user_data

    # SQLite serializes writers. Take the write lock before counting active tokens.
    await lock_user_data(user_id, db)
    now = to_db()
    count = await db.scalar(
        select(func.count()).select_from(APIToken).where(APIToken.user_id == user_id, _active_at(now)),
    )
    if count is not None and count >= MAX_ACTIVE_TOKENS:
        return None
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = APIToken(
        user_id=user_id, name=name, token_hash=_hash(token), token_prefix=token[:12], created_at=now,
        expires_at=now + timedelta(days=expires_in_days) if expires_in_days is not None else None,
    )
    db.add(row)
    await db.commit()
    return row, token


async def revoke_token(user_id: int, token_id: int, db: AsyncSession) -> bool:
    revoked = await db.scalar(delete(APIToken).where(APIToken.user_id == user_id, APIToken.id == token_id)
                              .returning(APIToken.id))
    await db.commit()
    return revoked is not None


async def find_token_user(token: str, db: AsyncSession) -> User | None:
    if not TOKEN_PATTERN.fullmatch(token):
        return None
    now = to_db()
    result = (await db.execute(
        select(User, APIToken.id, APIToken.last_used_at)
        .join(APIToken, APIToken.user_id == User.id)
        .where(APIToken.token_hash == _hash(token), _active_at(now)),
    )).one_or_none()
    if result is None:
        return None
    user, token_id, last_used = result
    cutoff = now - timedelta(minutes=1)
    if last_used is None or last_used <= cutoff:
        # Repeat the timestamp predicate in SQL to handle concurrent requests/workers.
        await db.execute(update(APIToken).where(
            APIToken.id == token_id, APIToken.token_hash == _hash(token), _active_at(now),
            or_(APIToken.last_used_at.is_(None), APIToken.last_used_at <= cutoff),
        ).values(last_used_at=now))
        await db.commit()
    return user
