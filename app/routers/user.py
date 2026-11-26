"""ユーザー・リスト・タグ APIルーター"""

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_access_token, require_authenticated_user, require_user_owner
from app.config import get_settings
from app.database import get_db
from app.models.db_models import User
from app.rate_limit import client_ip, limiter
from app.schemas.user import (
    CashRequest,
    ChangePasswordRequest,
    ConfirmPasswordRequest,
    ImportResult,
    ListCreateRequest,
    ListDetailResponse,
    ListItemAddRequest,
    ListItemResponse,
    ListResponse,
    ListsResponse,
    ListSummaryResponse,
    ListUpdateRequest,
    MessageResponse,
    SharesRequest,
    StockMemoRequest,
    StockMemoResponse,
    TagUpdateRequest,
    UserBackup,
    UserBackupData,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
)
from app.services import user_backup_service, user_service, watchlist_service
from app.timeutil import utcnow
from app.validation import SQLiteID, Symbol

router = APIRouter(prefix="/user", tags=["user"])


def user_response(user: User, version: int | None = None) -> UserResponse:
    token = create_access_token(user.id, user.token_version if version is None else version)
    return UserResponse(id=user.id, username=user.username, access_token=token)


def check_password_policy(password: str) -> None:
    try:
        user_service.validate_password(password)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


async def check_current_password(user: User, password: str, request: Request) -> None:
    settings = get_settings()
    ip = client_ip(request, settings)
    if settings.rate_limit_enabled:
        limiter.begin_login(ip, user.username, settings)
    valid = await user_service.verify_current_password(user, password)
    if settings.rate_limit_enabled:
        limiter.finish_login(ip, user.username, settings, success=valid)
    if not valid:
        raise HTTPException(400, "現在のパスワードが正しくありません")


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
    check_password_policy(req.password)

    try:
        user = await user_service.register_user(req.username, req.password, db)
        return user_response(user)
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
    return user_response(user)


@router.post("/{user_id}/password", response_model=UserResponse, summary="パスワードを変更")
async def change_password(
    req: ChangePasswordRequest, request: Request, user: User = Depends(require_user_owner),
    db: AsyncSession = Depends(get_db),
):
    check_password_policy(req.new_password)
    await check_current_password(user, req.current_password, request)
    version = await user_service.change_password(user, req.new_password, db)
    if version is None:
        require_authenticated_user(None)
    return user_response(user, version)


@router.post("/{user_id}/logout-all", status_code=204, summary="すべての端末からログアウト")
async def logout_all(user: User = Depends(require_user_owner), db: AsyncSession = Depends(get_db)):
    if await user_service.invalidate_tokens(user, db) is None:
        require_authenticated_user(None)
    await db.commit()


@router.delete("/{user_id}", status_code=204, summary="アカウントを削除")
async def delete_account(
    req: ConfirmPasswordRequest, request: Request, user: User = Depends(require_user_owner),
    db: AsyncSession = Depends(get_db),
):
    await check_current_password(user, req.current_password, request)
    if not await user_service.delete_account(user, db):
        require_authenticated_user(None)


@router.get("/{user_id}/export", response_model=UserBackupData, summary="アカウントデータをJSONで出力")
async def export_account(
    response: Response, user: User = Depends(require_user_owner), db: AsyncSession = Depends(get_db),
):
    response.headers["Content-Disposition"] = f'attachment; filename="watchlists-{utcnow().date()}.json"'
    return await user_backup_service.export_backup(user, db)


@router.post("/{user_id}/import", response_model=ImportResult, summary="アカウントデータをJSONから取り込む")
async def import_account(
    request: Request, mode: Literal["merge", "replace"] = "merge",
    user: User = Depends(require_user_owner), db: AsyncSession = Depends(get_db),
):
    maximum = get_settings().user_import_max_bytes
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > maximum:
            raise HTTPException(413, f"インポートファイルは {maximum} バイト以内にしてください")
        body.extend(chunk)
    try:
        backup = UserBackup.model_validate_json(body)
    except ValidationError as e:
        errors = [
            {"type": error["type"], "loc": ["body", *error["loc"]], "msg": "バックアップの形式が正しくありません"}
            for error in e.errors(include_input=False)
        ]
        raise HTTPException(422, errors) from e
    if backup.version not in (1, 2, 3, user_backup_service.BACKUP_VERSION):
        raise HTTPException(422, "対応していないバックアップのバージョンです")
    expected = {"alerts"} if backup.version >= 2 else set()
    if backup.version in (2, 3):
        expected.add("holdings")
    if backup.version >= 3:
        expected.add("backtest_runs")
    if backup.model_fields_set & {"holdings", "alerts", "backtest_runs"} != expected:
        raise HTTPException(422, "バックアップの形式が正しくありません")
    return await user_backup_service.import_backup(user.id, backup, mode, db)


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
        ],
        default_list_id=min(lists, key=lambda lst: (lst.created_at, lst.id)).id if lists else None,
    )


@router.post(
    "/{user_id}/lists",
    dependencies=[Depends(require_user_owner)],
    response_model=ListResponse,
    summary="リスト作成",
)
async def create_list(user_id: SQLiteID, req: ListCreateRequest, db: AsyncSession = Depends(get_db)):
    """新しいリストを作成"""
    try:
        lst = await user_service.create_list(user_id, req.name, db)
    except user_service.DuplicateListName as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
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


@router.get("/{user_id}/lists/{list_id}/summary", response_model=ListSummaryResponse, summary="銘柄リストの概要を取得")
async def list_summary(
    list_id: SQLiteID, request: Request, user: User = Depends(require_user_owner), db: AsyncSession = Depends(get_db),
    *, cached: bool = False,
):
    settings = get_settings()
    if not cached:
        limiter.data(request, settings, user_id=user.id)
    try:
        return await watchlist_service.get_list_summary(
            user.id, list_id, db, settings.watchlist_summary_concurrency, cached=cached,
        )
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.put("/{user_id}/lists/{list_id}/cash", response_model=CashRequest, summary="リストの現金を設定")
async def set_list_cash(
    list_id: SQLiteID, req: CashRequest, user: User = Depends(require_user_owner), db: AsyncSession = Depends(get_db),
):
    try:
        await user_service.set_list_cash(user.id, list_id, req.cash_jpy, db)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return req


@router.put("/{user_id}/lists/{list_id}/items/{symbol}/shares", response_model=SharesRequest, summary="銘柄の株数を設定")
async def set_item_shares(
    list_id: SQLiteID, symbol: Symbol, req: SharesRequest,
    user: User = Depends(require_user_owner), db: AsyncSession = Depends(get_db),
):
    try:
        await user_service.set_item_shares(user.id, list_id, symbol, req.shares, db)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return req


@router.get("/{user_id}/lists/{list_id}/export.csv", summary="銘柄リストをCSVで出力")
async def list_csv(list_id: SQLiteID, user: User = Depends(require_user_owner), db: AsyncSession = Depends(get_db)):
    try:
        content = await user_backup_service.export_list_csv(user.id, list_id, db)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return Response(
        content, media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="watchlist-{list_id}-{utcnow().date()}.csv"'},
    )


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
        name = user_service.normalize_list_name(req.name)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    try:
        lst = await user_service.update_list(user_id, list_id, name, db)
        return ListResponse(
            id=lst.id,
            name=lst.name,
            created_at=lst.created_at.isoformat(),
            updated_at=lst.updated_at.isoformat(),
        )
    except user_service.DuplicateListName as e:
        raise HTTPException(409, str(e)) from e
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
    except user_service.ProtectedList as e:
        raise HTTPException(400, str(e)) from e
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
            shares=item.shares,
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
            shares=item.shares,
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
