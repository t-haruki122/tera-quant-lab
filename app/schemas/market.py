"""経済指標一覧のレスポンス。"""

from pydantic import BaseModel

from app.market_indicators import MarketUnit


class MarketIndicatorResponse(BaseModel):
    symbol: str
    name: str
    group: str
    unit: MarketUnit
    note: str | None
    currency: str | None
    value: float | None
    previous_close: float | None
    change: float | None
    change_pct: float | None = None
    change_pt: float | None = None
    as_of: str | None
    sparkline: list[float]
    stale: bool
    error: str | None


class MarketOverviewResponse(BaseModel):
    as_of: str
    indicators: list[MarketIndicatorResponse]
