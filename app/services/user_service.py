"""ユーザー・リスト・タグ管理のサービスレイヤー"""

import asyncio
import hashlib
import hmac
import json
import re

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_authenticated_user
from app.models.db_models import PortfolioHolding, PriceAlert, StockMemo, User, UserList, UserListItem
from app.timeutil import to_db

ph = PasswordHasher()
_DUMMY_HASH = ph.hash("tql-dummy-password-for-missing-users")
DEFAULT_LIST_NAME = "マイリスト"


class DuplicateListName(ValueError):
    pass


class ProtectedList(ValueError):
    pass


def _hash_password(password: str) -> str:
    """パスワードをArgon2でハッシュ化"""
    return ph.hash(password)


def _verify_password(stored_hash: str, password: str) -> tuple[bool, bool]:
    """認証結果と再ハッシュの必要性を返す（旧SHA-256も移行対象）。"""
    if re.fullmatch(r"[0-9a-fA-F]{64}", stored_hash):
        digest = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return hmac.compare_digest(stored_hash.lower(), digest), True
    try:
        ph.verify(stored_hash, password)
        return True, ph.check_needs_rehash(stored_hash)
    except (VerificationError, InvalidHashError):
        return False, False


def validate_password(password: str) -> None:
    if len(password) < 8:
        raise ValueError("パスワードは8文字以上にしてください")


async def verify_current_password(user: User, password: str) -> bool:
    valid, _ = await asyncio.to_thread(_verify_password, user.password_hash, password)
    return valid


async def invalidate_tokens(user: User, db: AsyncSession, **values) -> int | None:
    """Reject a competing account change that already consumed this version."""
    return await db.scalar(
        update(User)
        .where(User.id == user.id, User.token_version == user.token_version)
        .values(token_version=User.token_version + 1, **values)
        .returning(User.token_version)
        .execution_options(synchronize_session=False)
    )


async def change_password(user: User, password: str, db: AsyncSession) -> int | None:
    password_hash = await asyncio.to_thread(_hash_password, password)
    version = await invalidate_tokens(user, db, password_hash=password_hash)
    await db.commit()
    return version


async def delete_account(user: User, db: AsyncSession) -> bool:
    if await invalidate_tokens(user, db) is None:
        return False
    await delete_user_data(user.id, db)
    await db.delete(user)
    await db.commit()
    return True


async def delete_user_data(user_id: int, db: AsyncSession, *, include_investments: bool = True) -> None:
    # SQLite connections use the default foreign_keys=OFF.
    lists = select(UserList.id).where(UserList.user_id == user_id)
    await db.execute(delete(UserListItem).where(UserListItem.list_id.in_(lists)))
    await db.execute(delete(UserList).where(UserList.user_id == user_id))
    await db.execute(delete(StockMemo).where(StockMemo.user_id == user_id))
    if include_investments:
        await db.execute(delete(PortfolioHolding).where(PortfolioHolding.user_id == user_id))
        await db.execute(delete(PriceAlert).where(PriceAlert.user_id == user_id))


async def lock_user_data(user_id: int, db: AsyncSession) -> None:
    # Acquire SQLite's write lock before checking limits or reading merge state.
    owner = await db.scalar(
        update(User).where(User.id == user_id).values(token_version=User.token_version)
        .returning(User.id)
        .execution_options(synchronize_session=False)
    )
    if owner is None:
        require_authenticated_user(None)


# ========== ユーザー ==========

async def register_user(username: str, password: str, db: AsyncSession) -> User:
    """ユーザー登録"""
    # 重複チェック
    result = await db.execute(select(User).where(User.username == username))
    existing = result.scalar_one_or_none()
    if existing:
        raise ValueError("このユーザー名は既に使用されています")

    user = User(
        username=username,
        password_hash=await asyncio.to_thread(_hash_password, password),
        created_at=to_db(),
    )
    db.add(user)
    await db.flush()  # IDを取得するためflush

    # デフォルトリストを自動作成
    now = to_db()
    default_list = UserList(
        user_id=user.id,
        name=DEFAULT_LIST_NAME,
        created_at=now,
        updated_at=now,
    )
    db.add(default_list)

    await db.commit()
    await db.refresh(user)
    return user


async def login_user(username: str, password: str, db: AsyncSession) -> User:
    """ログイン（ユーザー認証）"""
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user:
        # Match the Argon2 work for an existing user without blocking the event loop.
        await asyncio.to_thread(_verify_password, _DUMMY_HASH, password)
        raise ValueError("ユーザー名またはパスワードが正しくありません")
    valid, needs_rehash = await asyncio.to_thread(_verify_password, user.password_hash, password)
    if not valid:
        raise ValueError("ユーザー名またはパスワードが正しくありません")
    if needs_rehash:
        replacement = await asyncio.to_thread(_hash_password, password)
        await db.execute(
            update(User).where(User.id == user.id, User.password_hash == user.password_hash)
            .values(password_hash=replacement).execution_options(synchronize_session=False)
        )
        await db.commit()
    return user


async def get_or_create_default_list(user_id: int, db: AsyncSession) -> UserList:
    """ユーザーのデフォルトリストを取得（なければ作成）"""
    user_list = await find_default_list(user_id, db)
    if user_list:
        return user_list
    await lock_user_data(user_id, db)
    user_list = await find_default_list(user_id, db)
    if user_list:
        return user_list

    # リストがなければ作成
    now = to_db()
    user_list = UserList(
        user_id=user_id,
        name=DEFAULT_LIST_NAME,
        created_at=now,
        updated_at=now,
    )
    db.add(user_list)
    await db.commit()
    await db.refresh(user_list)
    return user_list


async def find_default_list(user_id: int, db: AsyncSession) -> UserList | None:
    return await db.scalar(
        select(UserList).where(UserList.user_id == user_id)
        .order_by(UserList.created_at, UserList.id).limit(1)
    )


def normalize_list_name(name: str) -> str:
    name = name.strip()
    if not 1 <= len(name) <= 50:
        raise ValueError("リスト名は1〜50文字で入力してください")
    return name


async def check_list_name(user_id: int, name: str, db: AsyncSession, *, exclude_id: int | None = None) -> None:
    query = select(UserList.name).where(UserList.user_id == user_id)
    if exclude_id is not None:
        query = query.where(UserList.id != exclude_id)
    if any(existing.strip() == name for existing in await db.scalars(query)):
        raise DuplicateListName("同じ名前のリストが既にあります")


# ========== リスト ==========

async def get_user_lists(user_id: int, db: AsyncSession) -> list[UserList]:
    """ユーザーのリスト一覧を取得"""
    result = await db.execute(
        select(UserList).where(UserList.user_id == user_id).order_by(UserList.updated_at.desc())
    )
    return list(result.scalars().all())


async def create_list(user_id: int, name: str, db: AsyncSession) -> UserList:
    """リストを作成"""
    name = normalize_list_name(name)
    await lock_user_data(user_id, db)
    await check_list_name(user_id, name, db)
    now = to_db()
    user_list = UserList(
        user_id=user_id,
        name=name,
        created_at=now,
        updated_at=now,
    )
    db.add(user_list)
    await db.commit()
    await db.refresh(user_list)
    return user_list


async def require_list(user_id: int, list_id: int, db: AsyncSession) -> UserList:
    result = await db.execute(
        select(UserList).where(UserList.id == list_id, UserList.user_id == user_id)
    )
    user_list = result.scalar_one_or_none()
    if not user_list:
        raise ValueError("リストが見つかりません")
    return user_list


async def get_list_items(list_id: int, db: AsyncSession) -> list[UserListItem]:
    return list(await db.scalars(
        select(UserListItem).where(UserListItem.list_id == list_id).order_by(UserListItem.id)
    ))


async def get_list_detail(user_id: int, list_id: int, db: AsyncSession) -> dict:
    """リスト詳細（アイテム含む）を取得"""
    user_list = await require_list(user_id, list_id, db)
    items = await get_list_items(list_id, db)

    return {
        "id": user_list.id,
        "name": user_list.name,
        "items": [
            {
                "symbol": item.symbol,
                "tags": json.loads(item.tags) if item.tags else [],
            }
            for item in items
        ],
        "created_at": user_list.created_at.isoformat(),
        "updated_at": user_list.updated_at.isoformat(),
    }


async def update_list(user_id: int, list_id: int, name: str, db: AsyncSession) -> UserList:
    """リスト名を更新"""
    await lock_user_data(user_id, db)
    user_list = await require_list(user_id, list_id, db)
    await check_list_name(user_id, name, db, exclude_id=list_id)

    user_list.name = name
    user_list.updated_at = to_db()
    await db.commit()
    await db.refresh(user_list)
    return user_list


async def delete_list(user_id: int, list_id: int, db: AsyncSession) -> None:
    """リストを削除（アイテムもCASCADE削除）"""
    await lock_user_data(user_id, db)
    user_list = await require_list(user_id, list_id, db)
    if user_list is await find_default_list(user_id, db):
        raise ProtectedList("デフォルトリスト（最後の1件を含む）は削除できません")

    # アイテムを先に削除（CASCADEが効かないDB向け対応）
    await db.execute(
        delete(UserListItem).where(UserListItem.list_id == list_id)
    )
    await db.delete(user_list)
    await db.commit()


# ========== リストアイテム ==========

async def add_list_item(
    user_id: int, list_id: int, symbol: str, tags: list[str], db: AsyncSession
) -> UserListItem:
    """リストにアイテムを追加"""
    symbol = symbol.upper()
    await lock_user_data(user_id, db)
    # リスト所有権チェック
    result = await db.execute(
        select(UserList).where(UserList.id == list_id, UserList.user_id == user_id)
    )
    if not result.scalar_one_or_none():
        raise ValueError("リストが見つかりません")

    # 重複チェック
    result = await db.execute(
        select(UserListItem).where(
            UserListItem.list_id == list_id, UserListItem.symbol == symbol
        )
    )
    if result.scalar_one_or_none():
        raise ValueError(f"{symbol} は既にリストに存在します")

    item = UserListItem(
        list_id=list_id,
        symbol=symbol.upper(),
        tags=json.dumps(tags, ensure_ascii=False) if tags else None,
        added_at=to_db(),
    )
    db.add(item)

    # リストの updated_at を更新
    list_result = await db.execute(
        select(UserList).where(UserList.id == list_id)
    )
    user_list = list_result.scalar_one()
    user_list.updated_at = to_db()

    await db.commit()
    await db.refresh(item)
    return item


async def remove_list_item(
    user_id: int, list_id: int, symbol: str, db: AsyncSession
) -> None:
    """リストからアイテムを削除"""
    symbol = symbol.upper()
    await lock_user_data(user_id, db)
    # リスト所有権チェック
    result = await db.execute(
        select(UserList).where(UserList.id == list_id, UserList.user_id == user_id)
    )
    if not result.scalar_one_or_none():
        raise ValueError("リストが見つかりません")

    result = await db.execute(
        select(UserListItem).where(
            UserListItem.list_id == list_id, UserListItem.symbol == symbol.upper()
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise ValueError(f"{symbol} はリストに存在しません")

    await db.delete(item)
    await db.commit()


async def update_item_tags(
    user_id: int, list_id: int, symbol: str, tags: list[str], db: AsyncSession
) -> UserListItem:
    """アイテムのタグを更新"""
    symbol = symbol.upper()
    await lock_user_data(user_id, db)
    # リスト所有権チェック
    result = await db.execute(
        select(UserList).where(UserList.id == list_id, UserList.user_id == user_id)
    )
    if not result.scalar_one_or_none():
        raise ValueError("リストが見つかりません")

    result = await db.execute(
        select(UserListItem).where(
            UserListItem.list_id == list_id, UserListItem.symbol == symbol.upper()
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise ValueError(f"{symbol} はリストに存在しません")

    item.tags = json.dumps(tags, ensure_ascii=False) if tags else None
    await db.commit()
    await db.refresh(item)
    return item

# ========== 銘柄メモ ==========

async def get_stock_memo(user_id: int, symbol: str, db: AsyncSession) -> StockMemo | None:
    """ユーザーの銘柄メモを取得"""
    symbol = symbol.upper()
    result = await db.execute(
        select(StockMemo).where(
            StockMemo.user_id == user_id,
            StockMemo.symbol == symbol.upper(),
        )
    )
    return result.scalar_one_or_none()


async def save_stock_memo(
    user_id: int,
    symbol: str,
    memo: str | None,
    db: AsyncSession,
) -> StockMemo:
    """ユーザーの銘柄メモを保存（作成または更新）"""
    await lock_user_data(user_id, db)
    symbol = symbol.upper()
    normalized_symbol = symbol.upper()
    existing_memo = await get_stock_memo(user_id, normalized_symbol, db)

    if existing_memo:
        existing_memo.memo = memo
        existing_memo.updated_at = to_db()
        await db.commit()
        await db.refresh(existing_memo)
        return existing_memo

    new_memo = StockMemo(
        user_id=user_id,
        symbol=normalized_symbol,
        memo=memo,
    )
    db.add(new_memo)
    await db.commit()
    await db.refresh(new_memo)
    return new_memo
