"""Session-only token management and read-client identity discovery."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_read_user, require_user_owner
from app.database import get_db
from app.models.db_models import APIToken, User
from app.services import api_token_service
from app.timeutil import to_db, utc_iso
from app.validation import SQLiteID

router = APIRouter(prefix="/user", tags=["user"])


class CreateTokenRequest(BaseModel):
    name: str
    expires_in_days: Literal[30, 90, 365] | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        name = value.strip()
        if not 1 <= len(name) <= 50:
            raise ValueError("トークン名は前後の空白を除いて1〜50文字で入力してください")
        return name


class TokenResponse(BaseModel):
    id: int
    name: str
    token_prefix: str
    created_at: str
    last_used_at: str | None
    expires_at: str | None
    expired: bool

    @classmethod
    def from_row(cls, row: APIToken):
        return cls(
            id=row.id, name=row.name, token_prefix=row.token_prefix,
            created_at=utc_iso(row.created_at),
            last_used_at=utc_iso(row.last_used_at) if row.last_used_at else None,
            expires_at=utc_iso(row.expires_at) if row.expires_at else None,
            expired=row.expires_at is not None and row.expires_at <= to_db(),
        )


class CreatedTokenResponse(TokenResponse):
    token: str


@router.get("/me", summary="認証中のユーザーIDを取得")
async def get_identity(response: Response, user: User = Depends(get_read_user)) -> dict[str, int]:
    response.headers["Cache-Control"] = "no-store"
    return {"id": user.id}


@router.get("/{user_id}/api-tokens", summary="APIトークン一覧を取得")
async def list_api_tokens(
    response: Response, user: User = Depends(require_user_owner), db: AsyncSession = Depends(get_db),
) -> list[TokenResponse]:
    response.headers["Cache-Control"] = "no-store"
    return [TokenResponse.from_row(row) for row in await api_token_service.list_tokens(user.id, db)]


@router.post("/{user_id}/api-tokens", status_code=201, summary="APIトークンを作成")
async def create_api_token(
    body: CreateTokenRequest, response: Response, user: User = Depends(require_user_owner),
    db: AsyncSession = Depends(get_db),
) -> CreatedTokenResponse:
    created = await api_token_service.create_token(user.id, body.name, body.expires_in_days, db)
    if created is None:
        raise HTTPException(409, f"有効なAPIトークンは最大 {api_token_service.MAX_ACTIVE_TOKENS} 件まで作成できます")
    row, token = created
    response.headers["Cache-Control"] = "no-store"
    return CreatedTokenResponse(**TokenResponse.from_row(row).model_dump(), token=token)


@router.delete("/{user_id}/api-tokens/{token_id}", status_code=204, summary="APIトークンを無効化")
async def revoke_api_token(
    token_id: SQLiteID, user: User = Depends(require_user_owner), db: AsyncSession = Depends(get_db),
) -> None:
    if not await api_token_service.revoke_token(user.id, token_id, db):
        raise HTTPException(404, "APIトークンが見つかりません")
