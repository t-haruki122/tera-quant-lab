"""Bounded Cartesian strategy search over one already-fetched price frame."""

from dataclasses import dataclass, fields
from itertools import product
from math import isnan, prod

from .engine import Backtester, BacktestResult
from .metrics import BacktestMetrics
from .strategies import make_strategy, validate_params

RANK_METRICS = {f.name for f in fields(BacktestMetrics)} - {"symbol"}
MAX_GRID_COMBOS = 500
LOWER_IS_BETTER = {"annualised_volatility", "max_drawdown", "max_drawdown_duration", "open_fallbacks",
                   "missing_eps_bars", "benchmark_max_drawdown", "benchmark_open_fallbacks"}


@dataclass
class GridResult:
    params: dict
    result: BacktestResult


def grid_params(strategy, base_params, grid, *, max_combos=MAX_GRID_COMBOS):
    if any(not values for values in grid.values()):
        raise ValueError("グリッドには各パラメータの候補を1件以上指定してください")
    count = prod(len(values) for values in grid.values())
    if count > max_combos:
        raise ValueError(f"グリッドの組み合わせが {count} 件あります。上限は {max_combos} 件です")
    return [validate_params(strategy, {**base_params, **dict(zip(grid, values, strict=True))})
            for values in product(*grid.values())]


def run_grid(symbol, price_df, strategy="sma_cross", base_params=None, grid=None, rank_by="sharpe",
             context=None, progress_callback=None, cancel_check=None, **engine_options):
    if rank_by not in RANK_METRICS:
        raise ValueError(f"Unknown rank metric {rank_by!r}; valid metrics: {', '.join(sorted(RANK_METRICS))}")
    combinations = grid_params(strategy, base_params or {}, grid or {})
    results = []
    for idx, params in enumerate(combinations):
        if cancel_check and cancel_check():
            raise RuntimeError("Cancelled")
        engine = Backtester(make_strategy(strategy, params), **engine_options)
        result = engine.run(symbol, price_df, context, cancel_check=cancel_check)
        results.append(GridResult(params, result))
        if progress_callback:
            progress_callback(idx + 1, len(combinations), results[-1])

    def key(item):
        value = getattr(item.result.metrics, rank_by)
        if value is None or isnan(value):
            return True, 0
        return False, value if rank_by in LOWER_IS_BETTER else -value

    return sorted(results, key=key)
