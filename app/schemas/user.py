"""ユーザー・リスト・タグ関連のPydanticスキーマ"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictInt

from app.validation import Symbol

# --- ユーザー ---

class UserRegisterRequest(BaseModel):
    """ユーザー登録リクエスト"""
    username: str
    password: str


class UserLoginRequest(BaseModel):
    """ログインリクエスト"""
    username: str
    password: str


class ConfirmPasswordRequest(BaseModel):
    current_password: str


class ChangePasswordRequest(ConfirmPasswordRequest):
    new_password: str


class UserResponse(BaseModel):
    """ユーザーレスポンス"""
    id: int
    username: str
    access_token: str
    token_type: Literal["bearer"] = "bearer"


# --- リスト ---

class ListCreateRequest(BaseModel):
    """リスト作成リクエスト"""
    name: str


class ListUpdateRequest(BaseModel):
    """リスト更新リクエスト"""
    name: str


class ListItemAddRequest(BaseModel):
    """リストアイテム追加リクエスト"""
    symbol: Symbol
    tags: list[str] = []


class TagUpdateRequest(BaseModel):
    """タグ更新リクエスト"""
    tags: list[str]


class ListItemResponse(BaseModel):
    """リストアイテムレスポンス"""
    symbol: str
    tags: list[str]


class ListResponse(BaseModel):
    """リストレスポンス"""
    id: int
    name: str
    created_at: str
    updated_at: str


class ListDetailResponse(BaseModel):
    """リスト詳細レスポンス（アイテム含む）"""
    id: int
    name: str
    items: list[ListItemResponse]
    created_at: str
    updated_at: str


class ListsResponse(BaseModel):
    """リスト一覧レスポンス"""
    lists: list[ListResponse]
    default_list_id: int | None = None


class MessageResponse(BaseModel):
    """汎用メッセージレスポンス"""
    message: str


class StockMemoRequest(BaseModel):
    """銘柄メモ保存リクエスト"""
    memo: str | None = None


class StockMemoResponse(BaseModel):
    """銘柄メモレスポンス"""
    symbol: str
    memo: str | None = None
    updated_at: str | None = None


class ListSummaryItem(ListItemResponse):
    name: str | None = None
    country: str | None = None
    sector: str | None = None
    price: float | None = None
    currency: str | None = None
    market_cap: int | float | None = None
    mix_index: float | None = None
    dividend_yield: float | None = None
    per: float | None = None
    pbr: float | None = None
    memo: str | None = None
    error: str | None = None


class ListSummaryResponse(ListResponse):
    items: list[ListSummaryItem]


class BackupModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class BackupItem(BackupModel):
    symbol: str
    tags: list[str]
    added_at: datetime | None


class BackupList(BackupModel):
    name: str
    items: list[BackupItem]


class BackupMemo(BackupModel):
    symbol: str
    memo: str | None
    updated_at: datetime


class UserBackup(BackupModel):
    version: StrictInt
    exported_at: datetime
    username: str
    lists: list[BackupList]
    memos: list[BackupMemo]


class ImportCounts(BaseModel):
    lists: int = 0
    items: int = 0
    tags: int = 0
    memos: int = 0


class SkippedEntry(BaseModel):
    entry: str
    reason: str


class ImportResult(BaseModel):
    imported: ImportCounts
    skipped: list[SkippedEntry]
