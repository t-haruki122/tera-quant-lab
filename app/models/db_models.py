"""SQLAlchemy データベースモデル"""

from datetime import date as UTCDate
from datetime import datetime
from typing import Literal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.database import Base
from app.timeutil import to_db


class SymbolDirectoryEntry(Base):
    __tablename__ = "symbol_directory"
    __table_args__ = (
        CheckConstraint("market IN ('JP', 'US')", name="ck_symbol_directory_market"),
        CheckConstraint("source IN ('jpx', 'nasdaqtrader')", name="ck_symbol_directory_source"),
    )

    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    search_key: Mapped[str] = mapped_column(String(300))
    market: Mapped[Literal["JP", "US"]] = mapped_column(String(2), index=True)
    segment: Mapped[str | None] = mapped_column(String(60))
    sector: Mapped[str | None] = mapped_column(String(100))
    is_etf: Mapped[bool] = mapped_column(Boolean)
    source: Mapped[Literal["jpx", "nasdaqtrader"]] = mapped_column(String(12))
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    __table_args__ = (
        CheckConstraint("kind IN ('single', 'grid', 'portfolio', 'walkforward')", name="ck_backtest_run_kind"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(12))
    request: Mapped[dict] = mapped_column(JSON)
    summary: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=to_db)
    note: Mapped[str | None] = mapped_column(Text)


class ApiStatsDaily(Base):
    __tablename__ = "api_stats_daily"

    date: Mapped[UTCDate] = mapped_column(Date, primary_key=True)
    endpoint: Mapped[str] = mapped_column(String, primary_key=True)
    hits: Mapped[int] = mapped_column(Integer)
    errors: Mapped[int] = mapped_column(Integer)
    total_ms: Mapped[float] = mapped_column(Float)


class SymbolMixin:
    @validates("symbol")
    def normalize_symbol(self, key, value):
        return value.upper()


class StockPrice(SymbolMixin, Base):
    """株価データ"""
    __tablename__ = "stock_prices"
    __table_args__ = (UniqueConstraint("symbol", "timestamp", "interval", name="uq_stock_prices_cache"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    interval: Mapped[str] = mapped_column(String(20), default="1d", server_default="1d", nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    open: Mapped[float | None] = mapped_column(Float, nullable=True)
    high: Mapped[float | None] = mapped_column(Float, nullable=True)
    low: Mapped[float | None] = mapped_column(Float, nullable=True)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    quote_name: Mapped[str | None] = mapped_column(String(200))
    quote_currency: Mapped[str | None] = mapped_column(String(10))
    quote_day_change: Mapped[float | None] = mapped_column(Float)
    quote_day_change_pct: Mapped[float | None] = mapped_column(Float)
    quote_market_time: Mapped[str | None] = mapped_column(String(32))
    quote_market_date: Mapped[str | None] = mapped_column(String(10))
    cached_at: Mapped[datetime] = mapped_column(
        DateTime, default=to_db, nullable=False
    )


class Financial(SymbolMixin, Base):
    """財務情報"""
    __tablename__ = "financials"
    __table_args__ = (UniqueConstraint("symbol", name="uq_financials_cache"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revenue: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    net_income: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    eps: Mapped[float | None] = mapped_column(Float, nullable=True)
    pe_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    assets: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    liabilities: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cached_at: Mapped[datetime] = mapped_column(
        DateTime, default=to_db, nullable=False
    )


class News(SymbolMixin, Base):
    """ニュース"""
    __tablename__ = "news"
    __table_args__ = (UniqueConstraint("symbol", "url", name="uq_news_cache"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cached_at: Mapped[datetime] = mapped_column(
        DateTime, default=to_db, nullable=False
    )


class CompanyProfile(SymbolMixin, Base):
    """会社プロフィール"""
    __tablename__ = "company_profiles"
    __table_args__ = (UniqueConstraint("symbol", name="uq_company_profiles_cache"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sector: Mapped[str | None] = mapped_column(String(100), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(200), nullable=True)
    employees: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    market_cap: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cached_at: Mapped[datetime] = mapped_column(
        DateTime, default=to_db, nullable=False
    )


class Indicator(SymbolMixin, Base):
    """投資指数"""
    __tablename__ = "indicators"
    __table_args__ = (UniqueConstraint("symbol", name="uq_indicators_cache"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    roe: Mapped[float | None] = mapped_column(Float, nullable=True)
    roa: Mapped[float | None] = mapped_column(Float, nullable=True)
    pbr: Mapped[float | None] = mapped_column(Float, nullable=True)
    per: Mapped[float | None] = mapped_column(Float, nullable=True)
    eps: Mapped[float | None] = mapped_column(Float, nullable=True)
    dividend_yield: Mapped[float | None] = mapped_column(Float, nullable=True)
    mix_index: Mapped[float | None] = mapped_column(Float, nullable=True)
    profit_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    debt_to_equity: Mapped[float | None] = mapped_column(Float, nullable=True)
    cached_at: Mapped[datetime] = mapped_column(
        DateTime, default=to_db, nullable=False
    )


class User(Base):
    """ユーザー"""
    __tablename__ = "users"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=to_db, nullable=False
    )


class APIToken(Base):
    __tablename__ = "api_tokens"
    __table_args__ = (
        CheckConstraint("length(name) BETWEEN 1 AND 50", name="ck_api_tokens_name"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(50))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    token_prefix: Mapped[str] = mapped_column(String(12))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=to_db)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)


class UserList(Base):
    """ユーザーの銘柄リスト"""
    __tablename__ = "user_lists"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    cash_jpy: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=to_db, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=to_db, onupdate=to_db, nullable=False
    )


class UserListItem(SymbolMixin, Base):
    """リスト内の銘柄アイテム"""
    __tablename__ = "user_list_items"
    __table_args__ = (
        UniqueConstraint("list_id", "symbol", name="uq_list_symbol"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    list_id: Mapped[int] = mapped_column(Integer, ForeignKey("user_lists.id", ondelete="CASCADE"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON文字列 例: '["テック","高配当"]'
    added_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    shares: Mapped[float | None] = mapped_column(Float)


class StockMemo(SymbolMixin, Base):
    """ユーザーごとの銘柄メモ"""
    __tablename__ = "stock_memos"
    __table_args__ = (
        UniqueConstraint("user_id", "symbol", name="uq_user_symbol_memo"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    memo: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=to_db, onupdate=to_db, nullable=False
    )


class PriceAlert(SymbolMixin, Base):
    __tablename__ = "price_alerts"
    __table_args__ = (
        CheckConstraint("kind IN ('above', 'below', 'change_pct')", name="ck_price_alert_kind"),
        CheckConstraint("threshold > 0", name="ck_price_alert_threshold"),
        CheckConstraint("trigger_count >= 0", name="ck_price_alert_trigger_count"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(10))
    threshold: Mapped[float] = mapped_column(Float)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=to_db)
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_triggered_market_date: Mapped[UTCDate | None] = mapped_column(Date)
    trigger_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    armed: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
