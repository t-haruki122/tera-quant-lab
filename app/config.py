"""アプリケーション設定管理"""

import json
from functools import lru_cache
from ipaddress import IPv4Network, IPv6Network, ip_network
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode

DEV_SECRET_KEY = "development-only-change-this-secret-key"
SECRET_KEY_MESSAGE = "SECRET_KEY に既定値以外の32文字以上の秘密鍵を設定してください。"


class Settings(BaseSettings):
    """環境変数から読み込む設定"""

    # Database
    database_url: str = "sqlite+aiosqlite:///./stock_data.db"

    # Authentication: override SECRET_KEY for every deployed instance.
    secret_key: str = Field(default=DEV_SECRET_KEY, min_length=1)
    access_token_expire_minutes: int = Field(default=1440, gt=0)
    user_import_max_bytes: int = Field(default=33_554_432, gt=0)
    watchlist_summary_concurrency: int = Field(default=8, gt=0)
    price_alert_max_per_user: int = Field(default=100, gt=0)
    alert_cooldown_minutes: int = Field(default=60, gt=0)

    # Same-origin frontend needs no CORS. Cookies are never enabled.
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    mcp_http_enabled: bool = True
    mcp_allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("cors_origins", "mcp_allowed_hosts", mode="before")
    @classmethod
    def parse_header_allowlist(cls, value):
        if isinstance(value, str):
            value = json.loads(value) if value.strip().startswith("[") else value.split(",")
        if isinstance(value, list):
            return [origin.strip() for origin in value if isinstance(origin, str) and origin.strip()]
        return value

    # Per-process sliding windows; all counts/windows must be positive.
    rate_limit_enabled: bool = True
    rate_limit_login_failures: int = Field(default=5, gt=0)
    rate_limit_login_window_seconds: int = Field(default=300, gt=0)
    rate_limit_login_ip_attempts: int = Field(default=20, gt=0)
    rate_limit_login_ip_window_seconds: int = Field(default=300, gt=0)
    rate_limit_register_attempts: int = Field(default=5, gt=0)
    rate_limit_register_window_seconds: int = Field(default=3600, gt=0)
    rate_limit_data_requests: int = Field(default=600, gt=0)
    rate_limit_data_window_seconds: int = Field(default=60, gt=0)
    history_max_span_years: int = Field(default=20, ge=1, le=9999)

    cache_retention_days: int = Field(default=30, gt=0)
    cache_stale_max_days: int = Field(default=7, ge=0)
    cache_warm_enabled: bool = False
    cache_warm_interval_minutes: int = Field(default=30, gt=0)
    cache_warm_concurrency: int = Field(default=2, gt=0)
    symbol_directory_enabled: bool = True
    symbol_directory_max_age_days: int = Field(default=7, gt=0)
    jquants_api_key: SecretStr | None = None
    jquants_enabled: bool = True
    jquants_requests_per_minute: int = Field(default=5, gt=0)
    jquants_base_url: str = "https://api.jquants.com/v2"
    jquants_history_years: int = Field(default=2, ge=1, le=20)
    jquants_delay_weeks: int = Field(default=12, ge=0, le=52)
    yfinance_cache_dir: str = ""
    stats_flush_seconds: float = Field(default=60, gt=0, allow_inf_nan=False)

    backtest_max_span_years: int = Field(default=20, ge=1, le=9999)
    backtest_grid_max_combos: int = Field(default=100, ge=1, le=500)
    backtest_timeout_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)
    backtest_max_concurrent_per_user: int = Field(default=1, gt=0)
    backtest_max_saved_runs: int = Field(default=50, gt=0)

    # Cache TTL (seconds)
    cache_ttl_current_price: int = 60        # 1分
    cache_ttl_history: int = 86400           # 1日
    cache_ttl_financials: int = 7776000      # 約90日（四半期）
    cache_ttl_news: int = 600                # 10分
    news_period: str = "30d"

    # Server
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: Literal["critical", "error", "warning", "info", "debug", "trace"] = "info"
    trusted_proxies: Annotated[list[IPv4Network | IPv6Network], NoDecode] = Field(default_factory=list)
    hsts_enabled: bool = False
    content_security_policy: str = ""
    docs_enabled: bool = True

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("trusted_proxies", mode="before")
    @classmethod
    def parse_trusted_proxies(cls, value):
        if isinstance(value, str):
            return [ip_network(part.strip(), strict=False) for part in value.split(",") if part.strip()]
        return value

    @field_validator("content_security_policy")
    @classmethod
    def validate_content_security_policy(cls, value: str) -> str:
        if any(not 32 <= ord(char) <= 126 for char in value):
            raise ValueError("CONTENT_SECURITY_POLICY は1行のASCII文字列で指定してください")
        return value.strip()

    # App
    app_name: str = "Stock Data API"
    app_version: str = "1.0.0"
    app_env: Literal["development", "production"] = "development"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @field_validator("jquants_api_key", mode="before")
    @classmethod
    def normalize_jquants_key(cls, value):
        return (value.strip() or None) if isinstance(value, str) else value

    @property
    def jquants_active(self) -> bool:
        return self.jquants_enabled and bool(self.jquants_api_key)

    def has_weak_secret(self) -> bool:
        return self.secret_key == DEV_SECRET_KEY or len(self.secret_key) < 32

    def validate_startup(self) -> None:
        if self.app_env == "production" and self.has_weak_secret():
            raise RuntimeError(f"APP_ENV=production: {SECRET_KEY_MESSAGE}")


@lru_cache
def get_settings() -> Settings:
    """設定のシングルトンインスタンスを取得"""
    return Settings()
