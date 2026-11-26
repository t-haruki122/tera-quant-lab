"""Export and atomically restore user-owned data."""

import csv
import io
import json
import re
from collections import Counter, defaultdict
from datetime import UTC
from decimal import Decimal
from math import isfinite

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.db_models import CompanyProfile, PriceAlert, StockMemo, User, UserList, UserListItem
from app.schemas.backtest import SaveRunRequest
from app.schemas.investments import AlertResponse
from app.schemas.user import (
    BackupAlert,
    BackupBacktestRun,
    BackupItem,
    BackupList,
    BackupMemo,
    ImportCounts,
    ImportResult,
    SkippedEntry,
    UserBackup,
    UserBackupData,
)
from app.services import backtest_runs_service, price_alert_service, user_service
from app.services.watchlist_service import get_list_memos
from app.timeutil import to_db, utcnow
from app.validation import normalize_symbol

BACKUP_VERSION = 4


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


async def export_backup(user: User, db: AsyncSession) -> UserBackupData:
    lists, items, memos = await read_user_data(user.id, db)
    return UserBackupData(
        version=BACKUP_VERSION,
        exported_at=utcnow(),
        username=user.username,
        lists=[
            BackupList(name=lst.name, cash_jpy=lst.cash_jpy, items=[
                BackupItem(
                    symbol=item.symbol,
                    shares=item.shares,
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
        alerts=[
            BackupAlert.model_validate(AlertResponse.model_validate(row).model_dump(exclude={"id"}))
            for row in await price_alert_service.alerts_for_user(user.id, db)
        ],
        backtest_runs=[
            BackupBacktestRun.model_validate(dict(
                name=row.name, kind=row.kind, request=row.request, result=row.result,
                created_at=row.created_at.replace(tzinfo=UTC), note=row.note,
            ))
            for row in await backtest_runs_service.list_runs(user.id, db, full=True)
        ],
    )


async def import_backup(user_id: int, backup: UserBackup, mode: str, db: AsyncSession) -> ImportResult:
    await user_service.lock_user_data(user_id, db)
    if mode == "replace":
        await user_service.delete_user_data(
            user_id, db, include_alerts=backup.version >= 2, include_backtests=backup.version >= 3,
        )
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
        if lst.cash_jpy is None and source.cash_jpy is not None:
            lst.cash_jpy = source.cash_jpy

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
            if item.shares is None and source_item.shares is not None:
                item.shares = source_item.shares
                counts.holdings += 1
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

    result = ImportResult(imported=counts, skipped=skipped)
    if backup.holdings:
        await import_legacy_holdings(user_id, backup, items, result, db)
    if backup.version >= 2:
        await import_alerts(user_id, backup, result, db)
    if backup.version >= 3:
        await import_backtests(user_id, backup, result, db)
    await db.commit()
    return result


async def import_backtests(user_id: int, backup: UserBackup, result: ImportResult, db: AsyncSession) -> None:
    existing = await backtest_runs_service.list_runs(user_id, db, full=True)
    maximum = get_settings().backtest_max_saved_runs
    for source in backup.backtest_runs:
        try:
            validated = SaveRunRequest.model_validate(source.model_dump(exclude={"created_at"}))
            row = backtest_runs_service.make_run(user_id, validated, created_at=source.created_at)
        except ValueError:
            result.skipped.append(SkippedEntry(entry=source.name, reason="バックテストの設定または結果の形式・サイズが正しくありません。"))
            continue
        if any((item.name, item.kind, item.created_at, item.note, item.request, item.result)
               == (row.name, row.kind, row.created_at, row.note, row.request, row.result) for item in existing):
            result.skipped.append(SkippedEntry(entry=row.name, reason="同じバックテストが保存済みです。"))
        elif len(existing) >= maximum:
            result.skipped.append(SkippedEntry(entry=row.name, reason="バックテストの保存上限に達しています。"))
        else:
            db.add(row)
            existing.append(row)
            result.imported.backtest_runs += 1


def alert_key(alert: PriceAlert | BackupAlert) -> tuple[str, str, float, bool, str | None]:
    return alert.symbol, alert.kind, alert.threshold, alert.enabled, alert.note


async def import_legacy_holdings(user_id: int, backup: UserBackup, items, result: ImportResult, db: AsyncSession) -> None:
    await db.flush()
    default_list = await user_service.find_default_list(user_id, db)
    if default_list is None:
        default_list = UserList(user_id=user_id, name=user_service.DEFAULT_LIST_NAME)
        db.add(default_list)
        await db.flush()
        result.imported.lists += 1

    default_items = items[default_list.id]
    normalized_items = {}
    for symbol, item in default_items.items():
        normalized_items.setdefault(symbol.upper(), item)
    for holding in backup.holdings:
        item = default_items.get(holding.symbol) or normalized_items.get(holding.symbol)
        if item is None:
            item = UserListItem(
                list_id=default_list.id, symbol=holding.symbol, added_at=to_db(holding.created_at),
            )
            default_items[holding.symbol] = item
            db.add(item)
            result.imported.items += 1
        shares = float(Decimal(str(item.shares or 0)) + Decimal(str(holding.shares)))
        if not isfinite(shares):
            result.skipped.append(SkippedEntry(entry=holding.symbol, reason="株数の合計が大きすぎます。"))
            continue
        item.symbol = holding.symbol
        item.shares = shares
        result.imported.holdings += 1


async def import_alerts(user_id: int, backup: UserBackup, result: ImportResult, db: AsyncSession) -> None:
    settings = get_settings()
    alerts = await price_alert_service.alerts_for_user(user_id, db)
    existing = Counter(alert_key(alert) for alert in alerts)
    for alert in backup.alerts:
        key = alert_key(alert)
        if existing[key]:
            existing[key] -= 1
        elif len(alerts) + result.imported.alerts >= settings.price_alert_max_per_user:
            result.skipped.append(SkippedEntry(
                entry=f"価格アラート / {alert.symbol}", reason="価格アラートの登録上限に達しています。",
            ))
        else:
            data = alert.model_dump()
            data.update(
                created_at=to_db(alert.created_at),
                last_triggered_at=to_db(alert.last_triggered_at) if alert.last_triggered_at else None,
            )
            if (alert.kind == "change_pct" and alert.last_triggered_at
                    and "last_triggered_market_date" not in alert.model_fields_set):
                data["last_triggered_market_date"] = to_db(alert.last_triggered_at).date()
            db.add(PriceAlert(user_id=user_id, **data))
            result.imported.alerts += 1


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
        select(UserListItem.symbol, CompanyProfile.name, UserListItem.tags, UserListItem.shares)
        .outerjoin(CompanyProfile, CompanyProfile.symbol == UserListItem.symbol)
        .where(UserListItem.list_id == list_id)
        .order_by(UserListItem.id)
    )
    output = io.StringIO(newline="")
    writer = csv.writer(output, quoting=csv.QUOTE_ALL)
    writer.writerow(("symbol", "name", "tags", "memo", "shares"))
    for symbol, name, tags, shares in rows:
        tag_text = ", ".join(json.loads(tags)) if tags else ""
        writer.writerow([*map(csv_cell, (symbol, name, tag_text, memos.get(symbol))), shares])
    return "\ufeff" + output.getvalue()
