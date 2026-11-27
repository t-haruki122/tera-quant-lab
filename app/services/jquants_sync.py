"""Persist J-Quants disclosures and checkpoint each fully fetched date."""

import asyncio
import logging
from collections.abc import Iterator
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum, auto
from uuid import uuid4

from sqlalchemy import Table, delete, func, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import database
from app.clients.jquants_client import (
    JQuantsClient,
    JQuantsError,
    JQuantsRequestError,
    Record,
    parse_master,
    parse_summary,
)
from app.config import Settings
from app.models.db_models import JQFinSummary, JQIssue, JQSyncState
from app.timeutil import to_db, utc_iso, utcnow
from app.validation import shift_years

logger = logging.getLogger(__name__)
_JST = timezone(timedelta(hours=9))
_DAY = timedelta(days=1)
_EDGE_TOLERANCE = timedelta(days=7)
_LEASE_KEY = "sync_lease"
_LEASE_DURATION = timedelta(minutes=10)
_LEASE_RENEWAL = timedelta(minutes=1)
_PROCESS_LOCK = asyncio.Lock()


def today_jst() -> date:
    return utcnow().astimezone(_JST).date()


def _days(start: date, end: date) -> Iterator[date]:
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        if day.weekday() < 5:
            yield day


def _plan_window(settings: Settings, today: date) -> tuple[date, date]:
    end = today - timedelta(weeks=settings.jquants_delay_weeks)
    return shift_years(end, -settings.jquants_history_years), end


def _window(settings: Settings, today: date, state: dict[str, str]) -> tuple[date, date]:
    start, end = _plan_window(settings, today)
    if edge := state.get("window_start"):
        start = max(start, date.fromisoformat(edge))
    if state.get("upper_rejected_on") == today.isoformat():
        end = min(end, date.fromisoformat(state["window_end"]))
    return start, end


async def _read_state(db: AsyncSession) -> dict[str, str]:
    rows = await db.execute(select(JQSyncState.key, JQSyncState.value).where(JQSyncState.key != _LEASE_KEY))
    return {key: value for key, value in rows}


async def _update_state(db: AsyncSession, values: dict[str, str]) -> None:
    stmt = insert(JQSyncState)
    await db.execute(stmt.on_conflict_do_update(index_elements=["key"], set_={"value": stmt.excluded.value}), [
        {"key": key, "value": value} for key, value in values.items()
    ])


class _LeaseLost(JQuantsError):
    pass


class _SyncLease:
    """Claim through SQLite so a CLI or another worker can see the same lease."""

    def __init__(self):
        self.token = uuid4().hex
        self.renew_at = datetime.min

    def _value(self, now: datetime) -> str:
        return (now + _LEASE_DURATION).isoformat(timespec="microseconds") + self.token

    def _owner(self):
        return (JQSyncState.key == _LEASE_KEY) & (func.substr(JQSyncState.value, 27) == self.token)

    async def claim(self) -> bool:
        now = to_db()
        stmt = insert(JQSyncState).values(key=_LEASE_KEY, value=self._value(now))
        claim = stmt.on_conflict_do_update(
            index_elements=["key"], set_={"value": stmt.excluded.value},
            where=func.substr(JQSyncState.value, 1, 26) <= now.isoformat(timespec="microseconds"),
        ).returning(JQSyncState.key)
        async with database.AsyncSessionLocal.begin() as db:
            acquired = await db.scalar(claim) == _LEASE_KEY
        self.renew_at = now + _LEASE_RENEWAL
        return acquired

    async def fence(self, db: AsyncSession) -> None:
        # This update also acquires the SQLite writer lock for the rest of the transaction.
        now = to_db()
        renew = update(JQSyncState).where(
            self._owner(), func.substr(JQSyncState.value, 1, 26) > now.isoformat(timespec="microseconds"),
        ).values(value=self._value(now)).returning(JQSyncState.key)
        if await db.scalar(renew) != _LEASE_KEY:
            raise _LeaseLost("J-Quants の同期ロックの有効期限が切れました")
        self.renew_at = now + _LEASE_RENEWAL

    async def before_request(self) -> None:
        if to_db() >= self.renew_at:
            async with database.AsyncSessionLocal.begin() as db:
                await self.fence(db)

    async def release(self) -> None:
        async with database.AsyncSessionLocal.begin() as db:
            await db.execute(delete(JQSyncState).where(self._owner()))


async def _upsert_rows(db: AsyncSession, model: type[JQIssue] | type[JQFinSummary], rows: list[Record]) -> None:
    if rows:
        table = model.__table__
        assert isinstance(table, Table)
        stmt = insert(table)
        await db.execute(stmt.on_conflict_do_update(
            index_elements=list(table.primary_key.columns),
            set_={column.name: stmt.excluded[column.name] for column in table.c if not column.primary_key},
        ), rows)


async def _store_progress(lease: _SyncLease, **values: str) -> None:
    async with database.AsyncSessionLocal.begin() as db:
        await lease.fence(db)
        await _update_state(db, values)


async def _refresh_master(client: JQuantsClient, lease: _SyncLease, today: date) -> None:
    rows = [parse_master(row) for row in await client.master()]
    now = to_db()
    async with database.AsyncSessionLocal.begin() as db:
        await lease.fence(db)
        await _upsert_rows(db, JQIssue, [{**row, "updated_at": now} for row in rows])
        await _update_state(db, {"last_master_refresh": today.isoformat(), "last_success_at": utc_iso(now)})
    logger.info("J-Quants の銘柄マスターを更新しました (%d 件)", len(rows))


async def _fetch_dates(
    client: JQuantsClient, lease: _SyncLease, state: dict[str, str], today: date, start: date, end: date,
    dates_from: date | None, dates_to: date | None,
) -> None:
    manual = dates_from is not None or dates_to is not None
    first = dates_from or start
    if not manual and (last_processed := state.get("processed_through", state.get("last_synced_date"))):
        first = max(start, date.fromisoformat(last_processed) + _DAY)
    days = list(_days(first, dates_to or end))
    lower_edge = first == start
    had_rejection = False
    for index, day in enumerate(days, 1):
        if index == 1 or index % 25 == 0 or index == len(days):
            logger.info("J-Quants の決算短信を同期しています (%d/%d 日、%s)", index, len(days), day)
        try:
            rows = [parse_summary(row) for row in await client.summaries(day)]
        except JQuantsRequestError as exc:
            if not exc.outside_window:
                raise
            rejection = {"last_rejected_date": day.isoformat(), "last_rejected_reason": "取得可能期間外"}
            if manual:
                had_rejection = True
                await _store_progress(lease, **rejection)
                logger.warning("J-Quants の取得可能期間外です (%s)", day)
                continue
            if lower_edge and day <= start + _EDGE_TOLERANCE:
                # Keep advancing across rejected weekdays at the oldest edge.
                await _store_progress(lease, **rejection, window_start=(day + _DAY).isoformat())
                logger.info("J-Quants の取得可能期間の開始日を調整しました (%s)", day)
                continue
            if day >= end - _EDGE_TOLERANCE:
                # Try the first unavailable recent date again on the next JST day.
                await _store_progress(
                    lease, **rejection, window_end=(day - _DAY).isoformat(), upper_rejected_on=today.isoformat(),
                )
                logger.info("J-Quants の取得可能期間の終了日を調整しました (%s)", day)
                break
            # An explicit restriction within the range should not strand subsequent dates.
            await _store_progress(lease, **rejection, processed_through=day.isoformat())
            logger.warning("J-Quants の取得可能期間外の開示日を記録しました (%s)", day)
            lower_edge = False
            continue

        lower_edge = False
        now = to_db()
        progress = {"last_success_at": utc_iso(now)}
        if not manual:
            progress["last_synced_date"] = day.isoformat()
            progress["processed_through"] = day.isoformat()
        async with database.AsyncSessionLocal.begin() as db:
            await lease.fence(db)
            await _upsert_rows(db, JQFinSummary, [{**row, "fetched_at": now} for row in rows])
            await _update_state(db, progress)
    if had_rejection:
        raise JQuantsError("指定した日付の一部が J-Quants の取得可能期間外でした")


class SyncResult(Enum):
    COMPLETE = auto()
    BUSY = auto()
    FAILED = auto()
    DISABLED = auto()


async def sync_jquants(
    settings: Settings, *, dates_from: date | None = None, dates_to: date | None = None,
) -> SyncResult:
    if not settings.jquants_active:
        return SyncResult.DISABLED
    if _PROCESS_LOCK.locked():
        return SyncResult.BUSY
    async with _PROCESS_LOCK:
        lease = _SyncLease()
        acquired = False
        try:
            acquired = await lease.claim()
            if not acquired:
                return SyncResult.BUSY
            today = today_jst()
            async with database.AsyncSessionLocal() as db:
                state = await _read_state(db)
            start, end = _window(settings, today, state)
            if (dates_from or start) > (dates_to or end) and (dates_from or dates_to):
                raise JQuantsError("J-Quants の取得開始日は終了日以前にしてください")
            await _store_progress(lease, window_start=start.isoformat(), window_end=end.isoformat())
            async with JQuantsClient(settings, before_request=lease.before_request) as client:
                if state.get("last_master_refresh") != today.isoformat():
                    await _refresh_master(client, lease, today)
                await _fetch_dates(client, lease, state, today, start, end, dates_from, dates_to)
            await _store_progress(lease, last_error="")
            return SyncResult.COMPLETE
        except Exception as exc:
            # In particular, an auth error ends the run immediately and logs a safe warning.
            message = str(exc) if isinstance(exc, JQuantsError) else "J-Quants の同期中に予期しないエラーが発生しました"
            logger.warning("%s", message)
            if acquired:
                try:
                    await _store_progress(lease, last_error=message)
                except Exception:
                    logger.warning("J-Quants の同期エラーを保存できませんでした")
            return SyncResult.FAILED
        finally:
            if acquired:
                try:
                    await lease.release()
                except Exception:
                    logger.warning("J-Quants の同期ロックを解放できませんでした")


async def sync_jquants_periodically(settings: Settings) -> None:
    while True:
        next_day = today_jst() + _DAY
        if await sync_jquants(settings) is SyncResult.BUSY:
            # A stopped worker's lease expires, so a restart can resume on the same date.
            await asyncio.sleep(60)
            continue
        next_run = datetime.combine(next_day, time.min, _JST)
        await asyncio.sleep(max(0, (next_run - utcnow()).total_seconds()))


async def sync_status(db: AsyncSession, settings: Settings, *, counts: bool = True) -> Record:
    active = settings.jquants_active
    state = await _read_state(db) if active else {}
    last_sync = state.get("last_synced_date")
    safe_error = state.get("last_error") or None
    if safe_error and settings.jquants_api_key:
        safe_error = safe_error.replace(settings.jquants_api_key.get_secret_value(), "[非表示]")
    status: Record = {
        "enabled": active, "configured": bool(settings.jquants_api_key), "last_synced_date": last_sync,
        "backfill_complete": False, "last_error": safe_error, "last_success_at": state.get("last_success_at"),
        "last_master_refresh": state.get("last_master_refresh"), "last_rejected_date": state.get("last_rejected_date"),
        "window": None, "progress": {"synced_days": 0, "total_days": 0},
    }
    if active:
        start, end = _window(settings, today_jst(), state)
        days = list(_days(start, end))
        last_processed = state.get("processed_through", last_sync)
        cursor = date.fromisoformat(last_processed) if last_processed else date.min
        synced = sum(day <= cursor for day in days)
        status.update(
            window={"start": start, "end": end}, progress={"synced_days": synced, "total_days": len(days)},
            backfill_complete=synced == len(days),
        )
    if counts:
        for name, model in (("issues_count", JQIssue), ("summaries_count", JQFinSummary)):
            status[name] = (await db.scalar(select(func.count()).select_from(model)) or 0) if active else 0
    return status
