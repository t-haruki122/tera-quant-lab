"""Cache the latest fund data and retain each observed daily NAV."""

from collections import OrderedDict
from dataclasses import asdict
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import PROVIDER_FAILURES, CacheWindow, stale_fields
from app.clients import fund_client
from app.clients.fund_client import UnsupportedFundError
from app.fund_catalog import CATALOG, FUND_SOURCE, FUND_UNIT
from app.models.db_models import FundNAV
from app.singleflight import coalesce
from app.stats import stats
from app.timeutil import to_db
from app.validation import FUND_CODE, fund_code

FUND_TTL_SECONDS = 6 * 60 * 60
# Successful requests are recorded in SQLite. Bound the in-memory cooldown for failed requests.
_failures: OrderedDict[tuple, tuple[datetime, type[Exception]]] = OrderedDict()


def latest_date_query(code):
    return select(func.max(FundNAV.base_date)).where(FundNAV.association_code == code)


def cached_fund(row: FundNAV, window: CacheWindow) -> dict:
    return {
        "association_code": row.association_code,
        "base_date": row.base_date,
        "nav": row.nav,
        "cancellation_price": row.cancellation_price,
        "net_assets": row.net_assets,
        "change": row.change,
        "change_pct": row.change_pct,
        "name": row.fund_name,
        "fund_cd": row.fund_cd,
        "isin": row.isin,
        "stats": row.stats,
        **window.metadata(row.fetched_at),
    }


def snapshot(symbol: str, data: dict | None = None, *, pending: bool = False, error: str | None = None) -> dict:
    code = fund_code(symbol)
    entry = CATALOG.get(code or "")
    fields = {
        "symbol": symbol, "asset_class": "投資信託", "association_code": code, "currency": "JPY",
        "unit": FUND_UNIT, "source_credit": FUND_SOURCE, "name": entry.name if entry else None,
    }
    if data is None:
        return {**fields, "pending": pending, "error": error}
    return {
        **fields, "price": data["nav"], "name": data["name"], "base_date": data["base_date"],
        "stale": data["stale"], "cached_at": data["cached_at"],
    }


@coalesce
async def get_fund(symbol: str, db: AsyncSession) -> dict:
    code = fund_code(symbol)
    if code is None or not FUND_CODE.fullmatch(code):
        raise UnsupportedFundError(code or symbol)

    window = CacheWindow(FUND_TTL_SECONDS)
    row = await db.scalar(
        select(FundNAV).where(FundNAV.association_code == code).order_by(FundNAV.base_date.desc()).limit(1),
    )
    if row is not None:
        last_checked = row.fetched_at
        if last_checked < window.fresh_since:
            last_checked = await db.scalar(select(func.max(FundNAV.fetched_at)).where(FundNAV.association_code == code))
        if last_checked is not None and last_checked >= window.fresh_since:
            stats.log_cache_hit()
            return cached_fund(row, window)

    key = (db.bind, code)
    try:
        if (failure := _failures.get(key)) and failure[0] >= window.fresh_since:
            kind = failure[1]
            if kind is UnsupportedFundError:
                raise UnsupportedFundError(code)
            raise kind()
        stats.log_api_call()
        try:
            data = await fund_client.fetch_latest(code)
        except (*PROVIDER_FAILURES, UnsupportedFundError) as error:
            _failures[key] = (to_db(), type(error))
            _failures.move_to_end(key)
            if len(_failures) > 1024:
                _failures.popitem(last=False)
            raise
    except PROVIDER_FAILURES as error:
        if row is None or row.fetched_at < window.usable_since:
            raise
        return {**cached_fund(row, window), **stale_fields(symbol, row.fetched_at, error)}

    _failures.pop(key, None)
    values = asdict(data)
    values["fetched_at"] = to_db()
    stmt = insert(FundNAV).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["association_code", "base_date"],
        set_={key: stmt.excluded[key] for key in values if key not in ("association_code", "base_date")},
    ).returning(FundNAV)
    saved = (await db.scalars(stmt)).one()
    latest = row if row is not None and row.base_date > saved.base_date else saved
    result = cached_fund(latest, window)
    await db.commit()
    return result


async def history(code: str, db: AsyncSession) -> list[dict]:
    rows = await db.execute(
        select(FundNAV.base_date, FundNAV.nav).where(FundNAV.association_code == code).order_by(FundNAV.base_date),
    )
    return [{"base_date": day, "nav": nav} for day, nav in rows]
