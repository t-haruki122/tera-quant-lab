"""Pydantic レスポンススキーマ"""

from datetime import date

from pydantic import BaseModel, Field

from app.market_indicators import MarketUnit


class CachedResponse(BaseModel):
    stale: bool | None = Field(default=None, exclude_if=lambda value: value is None)
    cached_at: str | None = Field(default=None, exclude_if=lambda value: value is None)


class StockSnapshot(CachedResponse):
    symbol: str
    asset_class: str | None = Field(default=None, exclude_if=lambda value: value is None)
    association_code: str | None = Field(default=None, exclude_if=lambda value: value is None)
    base_date: date | None = Field(default=None, exclude_if=lambda value: value is None)
    unit: str | None = Field(default=None, exclude_if=lambda value: value is None)
    source_credit: str | None = Field(default=None, exclude_if=lambda value: value is None)
    name: str | None = None
    country: str | None = None
    sector: str | None = None
    price: float | None = None
    currency: str | None = None
    market_cap: int | float | None = None
    mix_index: float | None = None
    roe: float | None = None
    dividend_yield: float | None = None
    per: float | None = None
    pbr: float | None = None
    pending: bool = False
    error: str | None = None


# --- 株価 ---

class StockPriceResponse(CachedResponse):
    """現在株価レスポンス"""
    symbol: str
    price: float
    timestamp: str
    currency: str | None = Field(default=None, exclude_if=lambda value: value is None)
    name: str | None = Field(default=None, exclude_if=lambda value: value is None)
    unit: MarketUnit | None = Field(default=None, exclude_if=lambda value: value is None)


# --- 過去株価 ---

class HistoryEntry(BaseModel):
    """過去株価の1レコード"""
    date: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float
    volume: int | None = None
    sma_25: float | None = None
    sma_50: float | None = None
    sma_75: float | None = None
    sma_200: float | None = None
    rsi_14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    bb_upper: float | None = None
    bb_middle: float | None = None
    bb_lower: float | None = None


class StockHistoryResponse(CachedResponse):
    """過去株価レスポンス"""
    symbol: str
    history: list[HistoryEntry]
    unit: MarketUnit | None = Field(default=None, exclude_if=lambda value: value is None)


# --- 財務情報 ---

class FinancialResponse(CachedResponse):
    """財務情報レスポンス"""
    symbol: str
    currency: str | None = Field(default=None, description="売上高・純利益の財務報告通貨。不明なら取引通貨")
    revenue: int | None = None
    net_income: int | None = None
    eps: float | None = None
    pe_ratio: float | None = None


class FinancialHistoryEntry(BaseModel):
    """過去財務情報の1レコード"""
    period: str
    period_end: str | None = None
    eps: float | None = None
    shares: float | None = None
    revenue: int | None = None
    net_income: int | None = None


class FinancialHistoryResponse(BaseModel):
    """過去財務情報レスポンス"""
    symbol: str
    currency: str | None = Field(default=None, description="売上高・純利益の財務報告通貨。不明なら取引通貨")
    history: list[FinancialHistoryEntry]


class DividendHistoryEntry(BaseModel):
    """過去配当情報の1レコード"""
    date: str
    dividend_per_share: float | None = None
    dividend_yield: float | None = None
    year_end_close: float | None = None


class DividendHistoryResponse(BaseModel):
    """過去配当情報レスポンス"""
    symbol: str
    history: list[DividendHistoryEntry]


# --- ニュース ---

class NewsEntry(BaseModel):
    """ニュースの1記事"""
    title: str
    url: str
    published_at: str | None = None
    publisher: str | None = None
    source: str | None = None


class NewsResponse(CachedResponse):
    """ニュースレスポンス"""
    symbol: str
    news: list[NewsEntry]


# --- 会社プロフィール ---

class CompanyProfileResponse(CachedResponse):
    """会社プロフィールレスポンス"""
    symbol: str
    name: str
    sector: str | None = None
    industry: str | None = None
    employees: int | None = None
    summary: str | None = None
    market_cap: int | None = None
    currency: str | None = None
    website: str | None = None
    country: str | None = None


# --- 投資指数 ---

class IndicatorsResponse(CachedResponse):
    """投資指数レスポンス"""
    symbol: str
    roe: float | None = None
    roa: float | None = None
    pbr: float | None = None
    per: float | None = None
    eps: float | None = None
    dividend_yield: float | None = None
    mix_index: float | None = None
    profit_margin: float | None = None
    debt_to_equity: float | None = Field(default=None, description="D/Eレシオ（%）。133.16 は 133.16%")


# --- エラー ---

class ErrorResponse(BaseModel):
    """エラーレスポンス"""
    detail: str
    status_code: int
