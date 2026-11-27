from datetime import date

from pydantic import BaseModel

from app.schemas.stock import CachedResponse


class FundNAVPoint(BaseModel):
    base_date: date
    nav: float


class FundExtreme(BaseModel):
    nav: float | None
    date: date | None


class FundStats(BaseModel):
    returns: dict[str, float | None]
    risk: dict[str, float | None]
    risk_return: dict[str, float | None]
    max_nav: dict[str, FundExtreme]
    min_nav: dict[str, FundExtreme]


class FundDetail(CachedResponse, FundNAVPoint):
    symbol: str
    association_code: str
    name: str
    fund_cd: str | None
    isin: str | None
    currency: str
    unit: str
    change: float | None
    change_pct: float | None
    cancellation_price: float | None
    net_assets: int | None
    source_credit: str
    stats: FundStats
    history: list[FundNAVPoint]
