"""Web backtests using the cached services and the shared simulation engine."""

import asyncio
import logging
import math
from collections import Counter
from dataclasses import asdict
from threading import Event, Lock

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import get_current_user, get_read_user
from app.backtest import REGISTRY, Backtester, history_to_df, make_strategy, run_grid
from app.backtest.engine import BacktestInputError
from app.backtest.grid import RANK_METRICS
from app.backtest.metrics import sample_curve
from app.backtest.portfolio import run_portfolio
from app.backtest.strategies import param_schema, warmup_days
from app.backtest.walkforward import run_walkforward
from app.config import get_settings
from app.database import get_db
from app.exceptions import ExternalAPIError, RateLimitError, SymbolNotFoundError, json_safe
from app.models.db_models import BacktestRun, User
from app.routers.dependencies import limit_backtest_requests, limit_read_backtest_requests, validate_optional_api_token
from app.schemas.backtest import (
    BacktestRequest,
    GridRequest,
    PortfolioRequest,
    RunRequest,
    SaveRunRequest,
    UpdateRunRequest,
    WalkForwardRequest,
)
from app.services import backtest_runs_service as saved
from app.services import finance_service, profile_service, stock_service, user_service
from app.validation import SQLiteID

router = APIRouter(prefix="/backtest", tags=["backtest"])
logger = logging.getLogger(__name__)
_active_jobs: Counter[int] = Counter()
_jobs_lock = Lock()


@router.get("/strategies", dependencies=[Depends(validate_optional_api_token)])
async def get_strategies():
    return {
        "strategies": [{"name": name, "params": param_schema(name)} for name in REGISTRY],
        "rank_metrics": sorted(RANK_METRICS),
    }


def _engine_options(body: BacktestRequest):
    return body.model_dump(include={"execution", "fee_rate", "slippage_bps", "initial_capital", "interval", "risk_free"})


def _single_curve(result):
    return [
        dict(date=date, equity=equity, benchmark_equity=benchmark)
        for date, equity, benchmark in zip(
            result.equity_curve["date"], result.equity_curve["equity"], result.benchmark_curve["equity"], strict=True,
        )
    ]


def _advanced_result(result, *, holdings=False):
    columns = ["date", "equity", "benchmark_equity"]
    if holdings:
        columns.append("holdings")
    warnings = list(result.warnings)
    if len(result.trades) > 5000:
        warnings.append(f"約定 {len(result.trades)} 件のうち、先頭5000件を表示しています。")
    return {
        "metrics": asdict(result.metrics), "benchmark_metrics": asdict(result.benchmark_metrics),
        "equity_curve": sample_curve(result.equity_curve[columns].to_dict("records"), 2000),
        "trades": result.trades[:5000], "warnings": warnings,
    }


def _simulate(body, history, context, cancel_check):
    options = _engine_options(body)
    if isinstance(body, PortfolioRequest):
        frames = {}
        for symbol, payload in history.items():
            try:
                frames[symbol] = history_to_df(payload)
            except (AttributeError, TypeError, ValueError) as exc:
                raise BacktestInputError(f"{symbol} の株価履歴の形式が正しくありません。") from exc
        portfolio = run_portfolio(
            frames, strategy=body.strategy, params=body.params, allocation=body.allocation,
            rebalance_every=body.rebalance_every, rebalance_threshold_pct=body.rebalance_threshold_pct,
            currencies={symbol: info["currency"] for symbol, info in context.items()},
            contexts=context, start_date=body.start_date.isoformat(),
            end_date=body.end_date.isoformat(), cancel_check=cancel_check, **options,
        )
        return json_safe({**_advanced_result(portfolio, holdings=True), "contributions": portfolio.contributions})
    prices = history_to_df(history)
    if isinstance(body, WalkForwardRequest):
        walk = run_walkforward(
            body.symbol, prices, strategy=body.strategy, params=body.params, grid=body.grid, rank_by=body.rank_by,
            folds=body.folds, window=body.window, context=context, end_date=body.end_date.isoformat(),
            cancel_check=cancel_check, **options,
        )
        return json_safe({
            **_advanced_result(walk), "robustness": walk.robustness,
            "folds": [{**row, "oos_metrics": asdict(row["oos_metrics"])} for row in walk.folds],
        })
    if isinstance(body, GridRequest):
        ranked = run_grid(body.symbol, prices, body.strategy, body.params, body.grid, body.rank_by,
                          context, cancel_check=cancel_check, **options)
        return json_safe({
            "rank_by": body.rank_by,
            "results": [{"rank": rank, "params": row.params, "metrics": asdict(row.result.metrics)}
                        for rank, row in enumerate(ranked, 1)],
            "equity_curve": sample_curve(_single_curve(ranked[0].result), 2000),
        })

    engine = Backtester(make_strategy(body.strategy, body.params), **options)
    result = engine.run(body.symbol, prices, context, cancel_check=cancel_check)
    benchmark = result.benchmark_curve.rename(columns={"equity": "benchmark_equity"})
    curve = result.equity_curve[["date", "equity"]].merge(
        benchmark[["date", "benchmark_equity"]],
        on="date", validate="one_to_one",
    )
    assert result.benchmark_metrics is not None
    return json_safe({
        "metrics": asdict(result.metrics),
        "benchmark_metrics": asdict(result.benchmark_metrics),
        "equity_curve": curve.to_dict("records"),
        "trades": result.trades,
        "warnings": result.warnings,
    })


async def _load_data(body: RunRequest | PortfolioRequest, db: AsyncSession):
    combinations = body.combinations() if isinstance(body, GridRequest) else [body.params]
    start, end = body.start_date.isoformat(), body.end_date.isoformat()
    warmup = warmup_days(body.interval, combinations)
    use_per = any(params.get("max_per") is not None for params in combinations)

    async def load(symbol, session):
        history = await stock_service.get_history(symbol, session, start, end, body.interval, warmup_days=warmup)
        context = {"start_date": start, "eps_lag_days": body.eps_lag_days}
        if use_per:
            context["financial_history"] = await finance_service.get_financial_history(symbol, session, limit=10)
        return history, context

    if isinstance(body, RunRequest):
        return await load(body.symbol, db)

    # The stock/finance services commit their caches. Give concurrent loads
    # independent sessions on the same database rather than sharing a session.
    sessions = async_sessionmaker(bind=db.bind, expire_on_commit=False)
    slots = asyncio.Semaphore(3)

    async def load_symbol(symbol):
        async with slots, sessions() as session:
            try:
                history, context = await load(symbol, session)
            except (ExternalAPIError, RateLimitError, SymbolNotFoundError, HTTPException, ValueError):
                return HTTPException(422, f"{symbol} の株価または財務データを取得できませんでした。")
            context["currency"] = await profile_service.get_trading_currency(symbol, session)
            return history, context

    items = await asyncio.gather(*(load_symbol(symbol) for symbol in body.symbols))
    histories, contexts = {}, {}
    for symbol, item in zip(body.symbols, items, strict=True):
        if isinstance(item, HTTPException):
            raise item
        histories[symbol], contexts[symbol] = item
    return histories, contexts


async def _run_job(body: RunRequest | PortfolioRequest, user_id: int, db: AsyncSession):
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
            history, context = await _load_data(body, db)
            try:
                return await asyncio.to_thread(_simulate, body, history, context, cancelled.is_set)
            except BacktestInputError as exc:
                raise HTTPException(422, str(exc)) from exc
            except (ValueError, OverflowError) as exc:
                logger.warning("Backtest calculation failed: %s", exc)
                raise HTTPException(422, "バックテストを計算できませんでした。入力値と期間を確認してください。") from exc
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
    body: RunRequest, user: User = Depends(limit_read_backtest_requests), db: AsyncSession = Depends(get_db),
):
    return await _run_job(body, user.id, db)


@router.post("/grid")
async def run_backtest_grid(
    body: GridRequest, user: User = Depends(limit_backtest_requests), db: AsyncSession = Depends(get_db),
):
    return await _run_job(body, user.id, db)


@router.post("/portfolio")
async def run_backtest_portfolio(
    body: PortfolioRequest, user: User = Depends(limit_backtest_requests), db: AsyncSession = Depends(get_db),
):
    return await _run_job(body, user.id, db)


@router.post("/walkforward")
async def run_backtest_walkforward(
    body: WalkForwardRequest, user: User = Depends(limit_backtest_requests), db: AsyncSession = Depends(get_db),
):
    return await _run_job(body, user.id, db)


async def _owned_run(run_id: int, user_id: int, db: AsyncSession) -> BacktestRun:
    row = await db.scalar(select(BacktestRun).where(BacktestRun.id == run_id, BacktestRun.user_id == user_id))
    if row is None:
        raise HTTPException(404, "保存済みのバックテストが見つかりません。")
    return row


@router.post("/runs", status_code=201)
async def save_run(
    body: SaveRunRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    try:
        row = saved.make_run(user.id, body)
    except saved.ResultTooLarge as exc:
        raise HTTPException(413, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    await user_service.lock_user_data(user.id, db)
    maximum = get_settings().backtest_max_saved_runs
    if await saved.count_runs(user.id, db) >= maximum:
        raise HTTPException(409, f"保存済みのバックテストは {maximum} 件までです。")
    db.add(row)
    await db.commit()
    return saved.serialize_run(row)


@router.get("/runs")
async def list_runs(user: User = Depends(get_read_user), db: AsyncSession = Depends(get_db)):
    return {"runs": [saved.serialize_run(row) for row in await saved.list_runs(user.id, db)]}


@router.get("/runs/{run_id}")
async def get_run(run_id: SQLiteID, user: User = Depends(get_read_user), db: AsyncSession = Depends(get_db)):
    return saved.serialize_run(await _owned_run(run_id, user.id, db), full=True)


@router.patch("/runs/{run_id}")
async def update_run(
    run_id: SQLiteID, body: UpdateRunRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    await user_service.lock_user_data(user.id, db)
    row = await _owned_run(run_id, user.id, db)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    await db.commit()
    return saved.serialize_run(row)


@router.delete("/runs/{run_id}", status_code=204)
async def delete_run(run_id: SQLiteID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await user_service.lock_user_data(user.id, db)
    await db.delete(await _owned_run(run_id, user.id, db))
    await db.commit()
