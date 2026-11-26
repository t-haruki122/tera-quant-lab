"""Validated requests shared by backtest execution and saved results."""

from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from app.backtest.grid import RANK_METRICS, grid_params
from app.backtest.portfolio import DEFAULT_REBALANCE_THRESHOLD_PCT, validate_symbols
from app.backtest.strategies import validate_params
from app.backtest.walkforward import walkforward_combinations
from app.config import get_settings
from app.validation import HistoryInterval, ISODate, Symbol, shift_years


class BacktestRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

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


class RunRequest(BacktestRequest):
    symbol: Symbol


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


class PortfolioRequest(BacktestRequest):
    symbols: list[Symbol] = Field(min_length=2, max_length=10)
    allocation: Literal["equal", "equal_active"] = "equal"
    rebalance_every: int | None = Field(default=None, ge=1, strict=True)
    rebalance_threshold_pct: float = Field(default=DEFAULT_REBALANCE_THRESHOLD_PCT, ge=0)

    @model_validator(mode="after")
    def validate_portfolio(self):
        self.symbols = validate_symbols(self.symbols)
        return self


class WalkForwardRequest(GridRequest):
    folds: int = Field(default=3, strict=True)
    window: Literal["rolling", "anchored"] = "rolling"

    def combinations(self):
        return walkforward_combinations(
            self.strategy, self.params, self.grid, self.rank_by, self.folds, self.window,
            max_combos=get_settings().backtest_grid_max_combos,
        )


RunKind = Literal["single", "grid", "portfolio", "walkforward"]
REQUEST_MODELS = {
    "single": RunRequest, "grid": GridRequest, "portfolio": PortfolioRequest, "walkforward": WalkForwardRequest,
}


def _run_name(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("結果名を入力してください。")
    return value


RunName = Annotated[str, Field(max_length=120), AfterValidator(_run_name)]
RunNote = Annotated[str, Field(max_length=2000)]


class SaveRunRequest(BaseModel):
    kind: RunKind
    name: RunName | None = None
    note: RunNote | None = None
    request: dict[str, Any]
    result: dict[str, Any]

    @model_validator(mode="after")
    def validate_saved_request(self):
        self.request = REQUEST_MODELS[self.kind].model_validate(self.request).model_dump(mode="json")
        return self


class UpdateRunRequest(BaseModel):
    name: RunName | None = None
    note: RunNote | None = None

    @model_validator(mode="after")
    def validate_update(self):
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("結果名を入力してください。")
        if not self.model_fields_set:
            raise ValueError("結果名またはメモを指定してください。")
        return self
