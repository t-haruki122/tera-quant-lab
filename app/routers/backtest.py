"""Web backtests using the cached services and the shared simulation engine."""

import asyncio
import math
from collections import Counter
from dataclasses import asdict
from threading import Event, Lock
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest import REGISTRY, Backtester, grid_params, history_to_df, make_strategy, run_grid, validate_params
from app.backtest.grid import RANK_METRICS
from app.backtest.strategies import param_schema
from app.config import get_settings
from app.database import get_db
from app.exceptions import json_safe
from app.models.db_models import User
from app.routers.dependencies import limit_backtest_requests
from app.services import finance_service, stock_service
from app.validation import HistoryInterval, ISODate, Symbol, shift_years

router = APIRouter(prefix="/backtest", tags=["backtest"])
_active_jobs: Counter[int] = Counter()
_jobs_lock = Lock()


class RunRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    symbol: Symbol
    start_date: ISODate
    end_date: ISODate
    interval: HistoryInterval = "1d"
    strategy: str = "sma_cross"
    params: dict[str, Any] = Field(default_factory=dict)
    execution: Literal["next_open", "close"] = "next_open"
    fee_rate: float = Field(0, ge=0, le=0.1)
    slippage_bps: float = Field(0, ge=0, le=1000)
    initial_capital: float = Field(1_000_000, gt=0)
    eps_lag_days: int = Field(90, ge=0, le=730)
    risk_free: float = Field(0, ge=-0.5, le=1)

    @model_validator(mode="after")
    def validate_request(self):
        start, end = self.start_date, self.end_date
        if start > end:
            raise ValueError("start_date は end_date 以前にしてください")
        years = get_settings().backtest_max_span_years
        if end > shift_years(start, years):
            raise ValueError(f"start_date から end_date までの期間は最大 {years} 年です")
        self.params = validate_params(self.strategy, self.params)
        return self


class GridRequest(RunRequest):
    grid: dict[str, list[Any]]
    rank_by: str = "sharpe"

    @model_validator(mode="after")
    def validate_grid(self):
        if self.rank_by not in RANK_METRICS:
            raise ValueError(f"rank_by は次のいずれかを指定してください: {', '.join(sorted(RANK_METRICS))}")
        self.combinations()
        return self

    def combinations(self):
        return grid_params(self.strategy, self.params, self.grid, max_combos=get_settings().backtest_grid_max_combos)


@router.get("/strategies")
async def get_strategies():
    return {
        "strategies": [{"name": name, "params": param_schema(name)} for name in REGISTRY],
        "rank_metrics": sorted(RANK_METRICS),
    }


def _warmup_days(interval, combinations):
    bars = max(
        max((params.get(key, 0) for key in ("long_window", "period", "window")), default=0)
        for params in combinations
    )
    # Daily bars omit holidays and weekends. Include a whole extra weekly or
    # monthly period so a partial first bucket cannot consume the warm-up.
    return (bars + 1) * {"1d": 2, "1wk": 7, "1mo": 31}[interval] + 7


def _simulate(body, history, context, cancel_check):
    prices = history_to_df(history)
    options = body.model_dump(include={"execution", "fee_rate", "slippage_bps", "initial_capital", "interval", "risk_free"})
    if isinstance(body, GridRequest):
        ranked = run_grid(body.symbol, prices, body.strategy, body.params, body.grid, body.rank_by,
                          context, cancel_check=cancel_check, **options)
        return json_safe({
            "rank_by": body.rank_by,
            "results": [{"rank": rank, "params": row.params, "metrics": asdict(row.result.metrics)}
                        for rank, row in enumerate(ranked, 1)],
        })

    engine = Backtester(make_strategy(body.strategy, body.params), **options)
    result = engine.run(body.symbol, prices, context, cancel_check=cancel_check)
    curve = result.equity_curve[["date", "equity"]].merge(
        result.benchmark_curve[["date", "equity"]].rename(columns={"equity": "benchmark_equity"}),
        on="date", validate="one_to_one",
    )
    return json_safe({
        "metrics": asdict(result.metrics),
        "benchmark_metrics": asdict(result.benchmark_metrics),
        "equity_curve": curve.to_dict("records"),
        "trades": result.trades,
        "warnings": result.warnings,
    })


async def _run_job(body: RunRequest, user_id: int, db: AsyncSession):
    settings = get_settings()
    with _jobs_lock:
        if _active_jobs[user_id] >= settings.backtest_max_concurrent_per_user:
            raise HTTPException(
                429, "実行中のバックテストが上限に達しました。しばらくしてから再試行してください。",
                headers={"Retry-After": str(max(1, math.ceil(settings.backtest_timeout_seconds)))},
            )
        _active_jobs[user_id] += 1

    cancelled = Event()
    deadline = asyncio.timeout(settings.backtest_timeout_seconds)
    try:
        async with deadline:
            combinations = body.combinations() if isinstance(body, GridRequest) else [body.params]
            start, end = body.start_date.isoformat(), body.end_date.isoformat()
            history = await stock_service.get_history(
                body.symbol, db, start, end, body.interval, warmup_days=_warmup_days(body.interval, combinations),
            )
            context = {"start_date": start, "eps_lag_days": body.eps_lag_days}
            if any(params.get("max_per") is not None for params in combinations):
                context["financial_history"] = await finance_service.get_financial_history(body.symbol, db, limit=10)
            try:
                return await asyncio.to_thread(_simulate, body, history, context, cancelled.is_set)
            except (ValueError, OverflowError) as exc:
                raise HTTPException(422, str(exc)) from exc
    except TimeoutError as exc:
        if not deadline.expired():
            raise
        raise HTTPException(504, "バックテストが制限時間を超えました。期間や条件を見直してください。") from exc
    finally:
        cancelled.set()
        with _jobs_lock:
            _active_jobs[user_id] -= 1
            if not _active_jobs[user_id]:
                del _active_jobs[user_id]


@router.post("/run")
async def run_backtest(
    body: RunRequest, user: User = Depends(limit_backtest_requests), db: AsyncSession = Depends(get_db),
):
    return await _run_job(body, user.id, db)


@router.post("/grid")
async def run_backtest_grid(
    body: GridRequest, user: User = Depends(limit_backtest_requests), db: AsyncSession = Depends(get_db),
):
    return await _run_job(body, user.id, db)
