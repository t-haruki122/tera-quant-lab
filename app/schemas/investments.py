"""保有銘柄と価格アラートの入出力。"""

from datetime import UTC, date, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.schemas.stock import CachedResponse
from app.timeutil import to_db
from app.validation import ISODate, Symbol

Note = Annotated[str, Field(max_length=2000)]
Shares = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
Cost = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]
AlertKind = Literal["above", "below", "change_pct"]
Threshold = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]


def as_utc(value: datetime) -> datetime:
    return to_db(value).replace(tzinfo=UTC)


UTCDateTime = Annotated[datetime, AfterValidator(as_utc)]


class HoldingFields(BaseModel):
    shares: Shares
    avg_cost: Cost
    acquired_on: ISODate | None = None
    note: Note | None = None


class HoldingCreate(HoldingFields):
    symbol: Symbol


class HoldingUpdate(HoldingFields):
    symbol: Symbol | None = None


class HoldingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    shares: float
    avg_cost: float
    acquired_on: date | None
    note: str | None
    created_at: UTCDateTime
    updated_at: UTCDateTime


class HoldingValuation(HoldingResponse, CachedResponse):
    name: str | None = None
    currency: str | None = None
    price: float | None = None
    market_value: float | None = None
    cost_basis: float | None = None
    unrealized_pl: float | None = None
    unrealized_pl_pct: float | None = None
    day_change: float | None = None
    day_change_pct: float | None = None
    weight_pct: float | None = None
    market_value_jpy: float | None = None
    cost_basis_jpy: float | None = None
    unrealized_pl_jpy: float | None = None
    fx_unavailable: bool = False
    error: str | None = None


class PortfolioTotals(BaseModel):
    market_value_jpy: float | None
    cost_basis_jpy: float | None
    unrealized_pl_jpy: float | None
    unrealized_pl_pct: float | None


class PortfolioResponse(CachedResponse):
    holdings: list[HoldingValuation]
    totals: PortfolioTotals
    partial: bool
    usd_jpy: float | None = None
    forex_stale: bool = False
    forex_cached_at: str | None = None


class AlertWrite(BaseModel):
    symbol: Symbol
    kind: AlertKind
    threshold: Threshold
    enabled: bool = True
    note: Note | None = None


class AlertResponse(AlertWrite):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: UTCDateTime
    last_triggered_at: UTCDateTime | None
    last_triggered_market_date: date | None
    trigger_count: int
    armed: bool


class AlertsResponse(BaseModel):
    alerts: list[AlertResponse]


class FiredAlert(AlertResponse):
    observed_value: float


class SkippedAlert(CachedResponse):
    id: int
    symbol: str
    reason: str


class AlertCheckResponse(BaseModel):
    fired: list[FiredAlert]
    skipped: list[SkippedAlert]
