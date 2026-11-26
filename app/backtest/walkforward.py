"""Optimize on historical windows and stitch the following out-of-sample equity."""

from dataclasses import dataclass
from math import isfinite, isnan, prod

import pandas as pd

from .engine import Backtester, BacktestInputError
from .grid import LOWER_IS_BETTER, MAX_GRID_COMBOS, RANK_METRICS, grid_params, run_grid
from .metrics import BacktestMetrics, calculate_metrics
from .strategies import make_strategy

MAX_WALKFORWARD_WORK = 500
MAX_FOLDS = 50


@dataclass(frozen=True)
class Fold:
    train_start: int
    train_stop: int
    test_stop: int


@dataclass
class WalkForwardResult:
    equity_curve: pd.DataFrame
    trades: list[dict]
    metrics: BacktestMetrics
    benchmark_metrics: BacktestMetrics
    folds: list[dict]
    robustness: dict
    warnings: list[str]


def _validate_folds(folds, window):
    if type(folds) is not int or not 2 <= folds <= MAX_FOLDS:
        raise BacktestInputError(f"分割数は2〜{MAX_FOLDS}の整数で指定してください。")
    if window not in ("rolling", "anchored"):
        raise BacktestInputError("学習期間の方式はローリングまたはアンカーを指定してください。")


def split_folds(bars: int, folds: int, window: str) -> list[Fold]:
    _validate_folds(folds, window)
    test_size = bars // (folds + 1)
    if test_size < 2:
        raise BacktestInputError(f"分割数 {folds} に対してバーが不足しています（最低 {2 * (folds + 1)} 本）。")
    train_size = bars - folds * test_size
    return [
        Fold(index * test_size if window == "rolling" else 0,
             train_size + index * test_size, train_size + (index + 1) * test_size)
        for index in range(folds)
    ]


def walkforward_combinations(strategy, params, grid, rank_by, folds, window, *, max_combos=MAX_GRID_COMBOS):
    _validate_folds(folds, window)
    if rank_by not in RANK_METRICS:
        raise BacktestInputError("順位の基準に使用できない指標が指定されました。")
    if folds * prod(map(len, grid.values())) > MAX_WALKFORWARD_WORK:
        raise BacktestInputError(f"分割数と組み合わせ数の積は {MAX_WALKFORWARD_WORK} 以下にしてください。")
    try:
        return grid_params(strategy, params, grid, max_combos=min(max_combos, MAX_GRID_COMBOS))
    except ValueError as exc:
        raise BacktestInputError(str(exc)) from exc


def run_walkforward(
    symbol: str, prices: pd.DataFrame, *, initial_capital: float, strategy: str = "sma_cross", params: dict | None = None,
    grid: dict | None = None, rank_by: str = "sharpe", folds: int = 3, window: str = "rolling",
    context: dict | None = None, end_date: str | None = None, cancel_check=None, **engine_options,
) -> WalkForwardResult:
    params, grid, context = params or {}, grid or {}, context or {}
    walkforward_combinations(strategy, params, grid, rank_by, folds, window)
    if prices.empty or not {"date", "close"} <= set(prices):
        raise BacktestInputError(f"{symbol} の指定期間に株価データがありません。")
    dates = prices["date"].tolist()
    if pd.isna(dates).any() or dates != sorted(set(dates)):
        raise BacktestInputError("株価データの日付は重複がなく昇順である必要があります。")
    eligible = [i for i, day in enumerate(dates)
                if (context.get("start_date") is None or day >= context["start_date"])
                and (end_date is None or day <= end_date)]
    boundaries = split_folds(len(eligible), folds, window)
    offset = eligible[0]
    initial = capital = initial_capital
    interval, risk_free = engine_options.get("interval", "1d"), engine_options.get("risk_free", 0.0)
    details, curves, trades, warnings = [], [], [], []
    paired_deltas = []
    for index, fold in enumerate(boundaries, 1):
        if cancel_check and cancel_check():
            raise RuntimeError("Cancelled")
        train_start, train_stop, test_stop = (offset + position
                                               for position in (fold.train_start, fold.train_stop, fold.test_stop))
        # Give each window its preceding bars for indicator warm-up. Slice at the
        # window's end before strategy preparation, so optimization cannot see OOS prices.
        ranked = run_grid(
            symbol, prices.iloc[:train_stop], strategy, params, grid, rank_by,
            {**context, "start_date": dates[train_start]}, cancel_check=cancel_check,
            initial_capital=initial, **engine_options,
        )
        chosen = ranked[0]
        del ranked
        is_value = getattr(chosen.result.metrics, rank_by)
        warning = None
        if is_value is None or isnan(is_value):
            warning = f"分割 {index}: 学習期間で有効な指標が得られなかったため、先頭のパラメータを使用しました。"
        engine = Backtester(make_strategy(strategy, chosen.params), **{**engine_options, "initial_capital": capital})
        oos = engine.run(symbol, prices.iloc[:test_stop], {**context, "start_date": dates[train_stop]},
                         cancel_check=cancel_check)
        capital = oos.metrics.final_asset
        curves.append(oos.equity_curve)
        trades.extend(oos.trades)
        oos_value = getattr(oos.metrics, rank_by)
        if is_value is not None and oos_value is not None and isfinite(is_value) and isfinite(oos_value):
            paired_deltas.append(oos_value - is_value if rank_by in LOWER_IS_BETTER else is_value - oos_value)
        details.append(dict(
            fold=index, train_start=dates[train_start], train_end=dates[train_stop - 1],
            test_start=dates[train_stop], test_end=dates[test_stop - 1], params=chosen.params,
            is_metric=is_value, oos_metrics=oos.metrics, warning=warning,
        ))
        warnings.extend(f"分割 {index}: {warning}" for warning in oos.warnings)

    curve = pd.concat(curves, ignore_index=True)
    benchmark = prices.iloc[offset + boundaries[0].train_stop:offset + boundaries[-1].test_stop].reset_index(drop=True)
    benchmark["signal"] = 0
    benchmark.loc[0, "signal"] = 1
    bench_engine = Backtester(make_strategy(strategy, params), initial_capital=initial, **engine_options)
    bench_curve, bench_trades, falls = bench_engine._simulate(symbol, benchmark, None, cancel_check)
    curve["benchmark_equity"] = bench_curve["equity"]
    metrics = calculate_metrics(symbol, curve, trades, initial, interval, risk_free)
    benchmark_metrics = calculate_metrics(symbol, bench_curve, bench_trades, initial, interval, risk_free)
    benchmark_metrics.open_fallbacks = falls
    metrics.benchmark_open_fallbacks = benchmark_metrics.open_fallbacks
    metrics.benchmark_return_pct = benchmark_metrics.return_pct
    metrics.benchmark_cagr = benchmark_metrics.cagr
    metrics.benchmark_max_drawdown = benchmark_metrics.max_drawdown
    metrics.open_fallbacks = sum(row["oos_metrics"].open_fallbacks for row in details)
    metrics.missing_eps_bars = sum(row["oos_metrics"].missing_eps_bars for row in details)
    robustness = dict(
        rank_by=rank_by, profitable_share=sum(row["oos_metrics"].return_pct > 0 for row in details) / folds,
        metric_degradation=sum(paired_deltas) / len(paired_deltas) if paired_deltas else None,
        paired_folds=len(paired_deltas),
    )
    return WalkForwardResult(curve, trades, metrics, benchmark_metrics, details, robustness, warnings)
