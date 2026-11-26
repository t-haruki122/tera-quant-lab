"""Backtest engine API with no CLI, GUI, plotting, network or printing side effects."""

from .engine import Backtester, BacktestResult, history_to_df
from .grid import GridResult, grid_params, run_grid
from .metrics import BacktestMetrics, calculate_metrics
from .strategies import REGISTRY, SmaCrossPerStrategy, Strategy, make_strategy, parse_assignments, validate_params

__all__ = ["Backtester", "BacktestResult", "BacktestMetrics", "GridResult", "history_to_df", "calculate_metrics",
           "grid_params", "run_grid", "REGISTRY", "SmaCrossPerStrategy", "Strategy", "make_strategy",
           "parse_assignments", "validate_params"]
