"""価格アラートの入出力。"""

from datetime import UTC, date, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.schemas.stock import CachedResponse
from app.timeutil import to_db
from app.validation import Symbol

Note = Annotated[str, Field(max_length=2000)]
AlertKind = Literal["above", "below", "change_pct"]
Threshold = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]


def as_utc(value: datetime) -> datetime:
    return to_db(value).replace(tzinfo=UTC)


UTCDateTime = Annotated[datetime, AfterValidator(as_utc)]


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
