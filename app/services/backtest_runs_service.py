"""Persist compact, user-supplied copies of backtest results."""

import json
from datetime import UTC, date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.backtest.metrics import sample_curve
from app.models.db_models import BacktestRun
from app.schemas.backtest import SaveRunRequest
from app.timeutil import to_db

MAX_RESULT_BYTES = 512 * 1024
SUMMARY_KEYS = ("return_pct", "cagr", "max_drawdown", "sharpe", "total_trades", "benchmark_return_pct")


class ResultTooLarge(ValueError):
    pass


def _valid_date(value):
    return isinstance(value, str) and date.fromisoformat(value).isoformat() == value


def compact_result(body: SaveRunRequest):
    result = dict(body.result)
    try:
        if "warnings" in result and (not isinstance(result["warnings"], list)
                                     or not all(isinstance(warning, str) for warning in result["warnings"])):
            raise ValueError
        curve = result["equity_curve"]
        if not isinstance(curve, list) or not curve:
            raise ValueError
        previous = ""
        for row in curve:
            if (not _valid_date(row["date"]) or row["date"] <= previous
                    or type(row["equity"]) not in (int, float) or row["equity"] <= 0
                    or type(row["benchmark_equity"]) not in (int, float) or row["benchmark_equity"] <= 0):
                raise ValueError
            previous = row["date"]
        result["equity_curve"] = sample_curve(curve, 500)
        if body.kind == "grid":
            rows = result["results"]
            if not isinstance(rows, list) or not rows or not all(isinstance(row["params"], dict) and
                                                                isinstance(row["metrics"], dict) for row in rows):
                raise ValueError
            metrics = rows[0]["metrics"]
        else:
            metrics = result["metrics"]
            if not isinstance(metrics, dict) or not isinstance(result["benchmark_metrics"], dict):
                raise ValueError
            trades = result["trades"]
            if not isinstance(trades, list) or not all(isinstance(row, dict) for row in trades):
                raise ValueError
            result["trades"] = trades[:1000]
            if len(trades) > 1000:
                result["warnings"] = [*result.get("warnings", []), "保存した取引履歴は先頭1000件です。"]
        if body.kind == "portfolio" and not isinstance(result["contributions"], dict):
            raise ValueError
        if body.kind == "walkforward":
            folds = result["folds"]
            if (not isinstance(result["robustness"], dict) or not isinstance(folds, list)
                    or not all(isinstance(row["params"], dict) and isinstance(row["oos_metrics"], dict) for row in folds)):
                raise ValueError
        size = len(json.dumps(result, allow_nan=False).encode("utf-8"))
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError("保存する結果の形式が正しくありません。") from exc
    if size > MAX_RESULT_BYTES:
        raise ResultTooLarge("保存用に縮小した結果は512 KiB以内にしてください。")
    summary = {key: metrics[key] if type(metrics.get(key)) in (int, float) else None for key in SUMMARY_KEYS}
    return result, summary


def make_run(user_id: int, body: SaveRunRequest, *, created_at=None) -> BacktestRun:
    result, summary = compact_result(body)
    created_at = to_db(created_at)
    name = body.name
    if name is None:
        strategy = body.request["strategy"]
        if body.kind == "portfolio":
            label = f"ポートフォリオ {len(body.request['symbols'])}銘柄 {strategy}"
        else:
            suffix = {"grid": " グリッド探索", "walkforward": " ウォークフォワード"}.get(body.kind, "")
            label = f"{body.request['symbol']} {strategy}{suffix}"
        name = f"{label} {created_at.date()}"
    return BacktestRun(user_id=user_id, name=name, kind=body.kind, request=body.request,
                       summary=summary, result=result, created_at=created_at, note=body.note)


async def count_runs(user_id: int, db: AsyncSession) -> int:
    return int(await db.scalar(select(func.count()).select_from(BacktestRun).where(BacktestRun.user_id == user_id)) or 0)


async def list_runs(user_id: int, db: AsyncSession, *, full=False):
    query = select(BacktestRun).where(BacktestRun.user_id == user_id).order_by(
        BacktestRun.created_at.desc(), BacktestRun.id.desc(),
    )
    if not full:
        query = query.options(defer(BacktestRun.result))
    return list(await db.scalars(query))


def serialize_run(row: BacktestRun, *, full=False):
    data = dict(id=row.id, name=row.name, kind=row.kind, summary=row.summary,
                metrics_source="client", created_at=row.created_at.replace(tzinfo=UTC), note=row.note)
    if full:
        return {**data, "request": row.request, "result": row.result}
    return {**data, **{key: row.request[key] for key in ("symbol", "symbols", "start_date", "end_date", "strategy")
                       if key in row.request}}
