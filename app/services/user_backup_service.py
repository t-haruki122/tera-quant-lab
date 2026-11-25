"""Export and atomically restore user-owned watchlists and memos."""

import csv
import io
import json
import re
from collections import defaultdict
from datetime import UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import CompanyProfile, StockMemo, User, UserList, UserListItem
from app.schemas.user import BackupItem, BackupList, BackupMemo, ImportCounts, ImportResult, SkippedEntry, UserBackup
from app.services import user_service
from app.services.watchlist_service import get_list_memos
from app.timeutil import to_db, utcnow
from app.validation import normalize_symbol

BACKUP_VERSION = 1


async def read_user_data(user_id: int, db: AsyncSession):
    lists = list(await db.scalars(
        select(UserList).where(UserList.user_id == user_id).order_by(UserList.created_at, UserList.id)
    ))
    items = defaultdict(dict)
    for item in await db.scalars(
        select(UserListItem).join(UserList).where(UserList.user_id == user_id).order_by(UserListItem.id)
    ):
        items[item.list_id][item.symbol] = item
    memos = {memo.symbol: memo for memo in await db.scalars(
        select(StockMemo).where(StockMemo.user_id == user_id).order_by(StockMemo.symbol)
    )}
    return lists, items, memos


async def export_backup(user: User, db: AsyncSession) -> UserBackup:
    lists, items, memos = await read_user_data(user.id, db)
    return UserBackup(
        version=BACKUP_VERSION,
        exported_at=utcnow(),
        username=user.username,
        lists=[
            BackupList(name=lst.name, items=[
                BackupItem(
                    symbol=item.symbol,
                    tags=json.loads(item.tags) if item.tags else [],
                    added_at=item.added_at.replace(tzinfo=UTC) if item.added_at else None,
                )
                for item in items[lst.id].values()
            ])
            for lst in lists
        ],
        memos=[
            BackupMemo(symbol=memo.symbol, memo=memo.memo, updated_at=memo.updated_at.replace(tzinfo=UTC))
            for memo in memos.values()
        ],
    )


async def import_backup(user_id: int, backup: UserBackup, mode: str, db: AsyncSession) -> ImportResult:
    await user_service.lock_watchlists(user_id, db)
    if mode == "replace":
        await user_service.delete_user_data(user_id, db)
    lists, items, memos = await read_user_data(user_id, db)
    by_name = {}
    for lst in lists:
        by_name.setdefault(lst.name.strip(), lst)
    counts = ImportCounts()
    skipped = []

    for source in backup.lists:
        try:
            name = user_service.normalize_list_name(source.name)
        except ValueError as exc:
            skipped.append(SkippedEntry(entry=source.name, reason=str(exc)))
            continue

        lst = by_name.get(name)
        if lst is None:
            lst = UserList(user_id=user_id, name=name)
            db.add(lst)
            await db.flush()
            by_name[name] = lst
            counts.lists += 1

        for source_item in source.items:
            try:
                symbol = normalize_symbol(source_item.symbol)
            except ValueError as exc:
                skipped.append(SkippedEntry(entry=f"{name} / {source_item.symbol}", reason=str(exc)))
                continue

            item = items[lst.id].get(symbol)
            if item is None:
                item = UserListItem(
                    list_id=lst.id, symbol=symbol,
                    added_at=to_db(source_item.added_at) if source_item.added_at else None,
                )
                items[lst.id][symbol] = item
                db.add(item)
                counts.items += 1
                tags = []
                additions = source_item.tags
            else:
                tags = json.loads(item.tags) if item.tags else []
                existing_tags = set(tags)
                additions = list(dict.fromkeys(tag for tag in source_item.tags if tag not in existing_tags))
            if additions:
                item.tags = json.dumps(tags + additions, ensure_ascii=False)
                counts.tags += len(additions)

    for source_memo in backup.memos:
        try:
            symbol = normalize_symbol(source_memo.symbol)
        except ValueError as exc:
            skipped.append(SkippedEntry(entry=f"メモ / {source_memo.symbol}", reason=str(exc)))
            continue
        memo = memos.get(symbol)
        if memo is None:
            memo = StockMemo(symbol=symbol, user_id=user_id)
            memos[symbol] = memo
            db.add(memo)
        elif (memo.memo and memo.memo.strip()) or not (source_memo.memo and source_memo.memo.strip()):
            continue
        memo.memo = source_memo.memo
        memo.updated_at = to_db(source_memo.updated_at)
        counts.memos += 1

    await db.commit()
    return ImportResult(imported=counts, skipped=skipped)


def csv_cell(value: str | None) -> str:
    text = value or ""
    # Mirror the quoted tab prefix used by the backtest CSV.
    if text.startswith(("\t", "\r", "\n")) or re.match(r"^[\s\x00-\x1f]*[=+@＝＋－＠-]", text):
        return "\t" + text
    return text


async def export_list_csv(user_id: int, list_id: int, db: AsyncSession) -> str:
    await user_service.require_list(user_id, list_id, db)
    memos = await get_list_memos(user_id, list_id, db)
    rows = await db.execute(
        select(UserListItem.symbol, CompanyProfile.name, UserListItem.tags)
        .outerjoin(CompanyProfile, CompanyProfile.symbol == UserListItem.symbol)
        .where(UserListItem.list_id == list_id)
        .order_by(UserListItem.id)
    )
    output = io.StringIO(newline="")
    writer = csv.writer(output, quoting=csv.QUOTE_ALL)
    writer.writerow(("symbol", "name", "tags", "memo"))
    for symbol, name, tags in rows:
        tag_text = ", ".join(json.loads(tags)) if tags else ""
        writer.writerow(map(csv_cell, (symbol, name, tag_text, memos.get(symbol))))
    return "\ufeff" + output.getvalue()
