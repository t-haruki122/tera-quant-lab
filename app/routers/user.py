"""ユーザー・リスト・タグ APIルーター"""

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_access_token, require_user_owner
from app.config import get_settings
from app.database import get_db
from app.rate_limit import client_ip, limiter
from app.schemas.user import (
    ListCreateRequest,
    ListDetailResponse,
    ListItemAddRequest,
    ListItemResponse,
    ListResponse,
    ListsResponse,
    ListUpdateRequest,
    MessageResponse,
    StockMemoRequest,
    StockMemoResponse,
    TagUpdateRequest,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
)
from app.services import user_service
from app.validation import SQLiteID, Symbol

router = APIRouter(prefix="/user", tags=["user"])


# ========== ユーザー認証 ==========

@router.post(
    "/register",
    response_model=UserResponse,
    summary="ユーザー登録",
    description="新しいユーザーアカウントを作成します。",
)
async def register(req: UserRegisterRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """ユーザー登録"""
    settings = get_settings()
    if settings.rate_limit_enabled:
        limiter.register(client_ip(request, settings), settings)
    if not req.username or len(req.username) < 2:
        raise HTTPException(status_code=400, detail="ユーザー名は2文字以上にしてください")
    if not req.password or len(req.password) < 8:
        raise HTTPException(status_code=400, detail="パスワードは8文字以上にしてください")

    try:
        user = await user_service.register_user(req.username, req.password, db)
        return UserResponse(id=user.id, username=user.username, access_token=create_access_token(user.id))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.post(
    "/login",
    response_model=UserResponse,
    summary="ログイン",
    description="ユーザー名とパスワードで認証し、ユーザー情報を返します。",
)
async def login(req: UserLoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """ログイン"""
    settings = get_settings()
    ip = client_ip(request, settings)
    if settings.rate_limit_enabled:
        limiter.begin_login(ip, req.username, settings)
    try:
        user = await user_service.login_user(req.username, req.password, db)
    except ValueError as e:
        if settings.rate_limit_enabled:
            limiter.finish_login(ip, req.username, settings, success=False)
        raise HTTPException(status_code=401, detail=str(e)) from e
    if settings.rate_limit_enabled:
        limiter.finish_login(ip, req.username, settings, success=True)
    return UserResponse(id=user.id, username=user.username, access_token=create_access_token(user.id))


@router.get(
    "/{user_id}/default-list",
    dependencies=[Depends(require_user_owner)],
    response_model=ListDetailResponse,
    summary="デフォルトリストを取得",
    description="ユーザーのデフォルト銘柄リストを取得します（なければ自動作成）。",
)
async def get_default_list(user_id: SQLiteID, db: AsyncSession = Depends(get_db)):
    """デフォルトリストを取得"""
    default_list = await user_service.get_or_create_default_list(user_id, db)
    try:
        return await user_service.get_list_detail(user_id, default_list.id, db)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# ========== リスト ==========

@router.get(
    "/{user_id}/lists",
    dependencies=[Depends(require_user_owner)],
    response_model=ListsResponse,
    summary="リスト一覧を取得",
)
async def get_lists(user_id: SQLiteID, db: AsyncSession = Depends(get_db)):
    """ユーザーのリスト一覧"""
    lists = await user_service.get_user_lists(user_id, db)
    return ListsResponse(
        lists=[
            ListResponse(
                id=lst.id,
                name=lst.name,
                created_at=lst.created_at.isoformat(),
                updated_at=lst.updated_at.isoformat(),
            )
            for lst in lists
        ]
    )


@router.post(
    "/{user_id}/lists",
    dependencies=[Depends(require_user_owner)],
    response_model=ListResponse,
    summary="リスト作成",
)
async def create_list(user_id: SQLiteID, req: ListCreateRequest, db: AsyncSession = Depends(get_db)):
    """新しいリストを作成"""
    if not req.name:
        raise HTTPException(status_code=400, detail="リスト名を入力してください")

    lst = await user_service.create_list(user_id, req.name, db)
    return ListResponse(
        id=lst.id,
        name=lst.name,
        created_at=lst.created_at.isoformat(),
        updated_at=lst.updated_at.isoformat(),
    )


@router.get(
    "/{user_id}/lists/{list_id}",
    dependencies=[Depends(require_user_owner)],
    response_model=ListDetailResponse,
    summary="リスト詳細を取得",
)
async def get_list_detail(user_id: SQLiteID, list_id: SQLiteID, db: AsyncSession = Depends(get_db)):
    """リスト詳細（アイテム含む）"""
    try:
        return await user_service.get_list_detail(user_id, list_id, db)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.put(
    "/{user_id}/lists/{list_id}",
    dependencies=[Depends(require_user_owner)],
    response_model=ListResponse,
    summary="リスト名を更新",
)
async def update_list(
    user_id: SQLiteID, list_id: SQLiteID, req: ListUpdateRequest, db: AsyncSession = Depends(get_db)
):
    """リスト名を変更"""
    try:
        lst = await user_service.update_list(user_id, list_id, req.name, db)
        return ListResponse(
            id=lst.id,
            name=lst.name,
            created_at=lst.created_at.isoformat(),
            updated_at=lst.updated_at.isoformat(),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.delete(
    "/{user_id}/lists/{list_id}",
    dependencies=[Depends(require_user_owner)],
    response_model=MessageResponse,
    summary="リストを削除",
)
async def delete_list(user_id: SQLiteID, list_id: SQLiteID, db: AsyncSession = Depends(get_db)):
    """リストを削除"""
    try:
        await user_service.delete_list(user_id, list_id, db)
        return MessageResponse(message="リストを削除しました")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# ========== リストアイテム ==========

@router.post(
    "/{user_id}/lists/{list_id}/items",
    dependencies=[Depends(require_user_owner)],
    response_model=ListItemResponse,
    summary="リストにアイテムを追加",
)
async def add_item(
    user_id: SQLiteID, list_id: SQLiteID, req: ListItemAddRequest, db: AsyncSession = Depends(get_db)
):
    """リストに銘柄を追加"""
    try:
        item = await user_service.add_list_item(user_id, list_id, req.symbol, req.tags, db)
        return ListItemResponse(
            symbol=item.symbol,
            tags=json.loads(item.tags) if item.tags else [],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete(
    "/{user_id}/lists/{list_id}/items/{symbol}",
    dependencies=[Depends(require_user_owner)],
    response_model=MessageResponse,
    summary="リストからアイテムを削除",
)
async def remove_item(
    user_id: SQLiteID, list_id: SQLiteID, symbol: Symbol, db: AsyncSession = Depends(get_db)
):
    """リストから銘柄を削除"""
    try:
        await user_service.remove_list_item(user_id, list_id, symbol, db)
        return MessageResponse(message=f"{symbol} をリストから削除しました")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.put(
    "/{user_id}/lists/{list_id}/items/{symbol}/tags",
    dependencies=[Depends(require_user_owner)],
    response_model=ListItemResponse,
    summary="アイテムのタグを更新",
)
async def update_tags(
    user_id: SQLiteID, list_id: SQLiteID, symbol: Symbol,
    req: TagUpdateRequest, db: AsyncSession = Depends(get_db),
):
    """銘柄のタグを更新"""
    try:
        item = await user_service.update_item_tags(user_id, list_id, symbol, req.tags, db)
        return ListItemResponse(
            symbol=item.symbol,
            tags=json.loads(item.tags) if item.tags else [],
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

# ========== 銘柄メモ ==========

@router.get(
    "/{user_id}/memo/{symbol}",
    dependencies=[Depends(require_user_owner)],
    response_model=StockMemoResponse,
    summary="銘柄メモを取得",
)
async def get_memo(user_id: SQLiteID, symbol: Symbol, db: AsyncSession = Depends(get_db)):
    """銘柄メモを取得"""
    memo = await user_service.get_stock_memo(user_id, symbol, db)
    if not memo:
        return StockMemoResponse(
            symbol=symbol,
            memo=None,
            updated_at=None,
        )
    return StockMemoResponse(
        symbol=memo.symbol,
        memo=memo.memo,
        updated_at=memo.updated_at.isoformat(),
    )


@router.put(
    "/{user_id}/memo/{symbol}",
    dependencies=[Depends(require_user_owner)],
    response_model=StockMemoResponse,
    summary="銘柄メモを保存",
)
async def save_memo(
    user_id: SQLiteID,
    symbol: Symbol,
    req: StockMemoRequest,
    db: AsyncSession = Depends(get_db)
):
    """銘柄メモを作成または更新"""
    memo = await user_service.save_stock_memo(
        user_id,
        symbol,
        req.memo,
        db,
    )
    return StockMemoResponse(
        symbol=memo.symbol,
        memo=memo.memo,
        updated_at=memo.updated_at.isoformat(),
    )
