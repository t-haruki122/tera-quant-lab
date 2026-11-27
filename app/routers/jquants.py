"""Local read access to J-Quants sync status and disclosure summaries."""

import re
from collections import defaultdict
from datetime import date, time, timedelta
from math import isfinite

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.jquants_client import Record
from app.config import get_settings
from app.database import get_db
from app.models.db_models import JQFinSummary
from app.routers.dependencies import limit_read_data_requests
from app.services.jquants_sync import sync_status
from app.timeutil import utc_iso
from app.validation import Symbol

router = APIRouter(tags=["J-Quants"], dependencies=[Depends(limit_read_data_requests)])


@router.get("/jquants/status")
async def get_jquants_status(db: AsyncSession = Depends(get_db)) -> Record:
    """J-Quants の同期進捗と取得件数を返します。"""
    return await sync_status(db, get_settings())


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or not denominator:
        return None
    value = numerator / denominator
    return value if isfinite(value) else None


def _disclosure_order(row: JQFinSummary) -> tuple[date, time, str]:
    return row.disclosed_date, row.disclosed_time or time.min, row.disc_no


def _summaries(rows: list[JQFinSummary]) -> list[Record]:
    annual: dict[tuple[bool | None, date], list[JQFinSummary]] = defaultdict(list)
    for row in rows:
        if row.period_type == "FY" and row.period_end and row.equity is not None:
            annual[row.consolidated, row.period_end].append(row)

    output = []
    for row in rows:
        roe_calc = None
        if row.period_type == "FY" and row.equity is not None:
            equity = row.equity
            if row.fy_start and row.fy_start > date.min:
                for previous in annual.get((row.consolidated, row.fy_start - timedelta(days=1)), []):
                    if previous.equity is not None and _disclosure_order(previous) < _disclosure_order(row):
                        equity = equity / 2 + previous.equity / 2
                        break
            roe_calc = _ratio(row.net_profit, equity)
        output.append({
            **{column.name: getattr(row, column.name) for column in JQFinSummary.__table__.columns},
            "fetched_at": utc_iso(row.fetched_at), "roe_calc": roe_calc,
            "operating_margin": _ratio(row.operating_profit, row.sales),
            "equity_ratio": row.equity_ratio if row.equity_ratio is not None else _ratio(row.equity, row.total_assets),
        })
    return output


@router.get("/stock/{symbol}/jq-financials")
async def get_jp_financials(symbol: Symbol, db: AsyncSession = Depends(get_db)) -> Record:
    """日本株の保存済み決算短信サマリーを開示日の新しい順に返します。"""
    settings = get_settings()
    delay = settings.jquants_delay_weeks
    result: Record = {
        "symbol": symbol, "source": "J-Quants", "currency": "JPY", "delay_weeks": delay,
        "delay_note": f"J-Quants 無料プランのデータは {delay} 週間遅れです。", "summaries": [],
    }
    if not settings.jquants_active:
        return {**result, "note": "J-Quants は無効です。"}
    if not re.fullmatch(r"[A-Z0-9]{4}\.T", symbol):
        return {**result, "note": "J-Quants は日本株が対象です。"}
    rows = list(await db.scalars(select(JQFinSummary).where(JQFinSummary.symbol == symbol).order_by(
        JQFinSummary.disclosed_date.desc(), JQFinSummary.disclosed_time.desc(), JQFinSummary.disc_no.desc(),
    )))
    if not rows:
        return {**result, "note": "この銘柄の保存済み決算短信はありません。"}
    return {**result, "summaries": _summaries(rows)}
