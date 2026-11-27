"""Responses for sector comparisons and fiscal-year valuations."""

from datetime import date
from typing import Literal

from pydantic import BaseModel

type SectorSource = Literal["jpx33", "yahoo_sector", "yahoo_industry"]
type MetricDirection = Literal["better", "cheaper", "neutral"]


class PeerMetric(BaseModel):
    key: str
    label: str
    value: float | None
    rank: int | None
    percentile: float | None
    median: float | None
    count: int
    higher_is: MetricDirection


class ValuationRatios(BaseModel):
    per: float | None = None
    pbr: float | None = None
    mix_index: float | None = None


class PeerCompany(ValuationRatios):
    symbol: str
    name: str | None
    roe: float | None = None
    dividend_yield: float | None = None
    market_cap: float | None = None


class PeerComparison(BaseModel):
    symbol: str
    sector_name: str | None = None
    sector_source: SectorSource
    peer_count: int = 0
    as_of: date | None = None
    metrics: list[PeerMetric] = []
    peers: list[PeerCompany] = []
    notes: list[str] = []


class ValuationPoint(ValuationRatios):
    period_end: date
    source: Literal["jquants", "yfinance"]
    eps: float | None
    bps: float | None
    roe: float | None
    statement_currency: str | None
    price: float | None = None
    price_date: date | None = None


class ValuationRange(BaseModel):
    min: float
    median: float
    max: float


class ValuationBand(BaseModel):
    per: ValuationRange | None = None
    pbr: ValuationRange | None = None


class ValuationHistory(BaseModel):
    symbol: str
    price_currency: str | None = None
    points: list[ValuationPoint] = []
    current: ValuationRatios = ValuationRatios()
    band: ValuationBand = ValuationBand()
    notes: list[str] = []
