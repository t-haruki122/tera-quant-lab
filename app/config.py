"""アプリケーション設定管理"""

import json
from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode

DEV_SECRET_KEY = "development-only-change-this-secret-key"


class Settings(BaseSettings):
    """環境変数から読み込む設定"""

    # Database
    database_url: str = "sqlite+aiosqlite:///./stock_data.db"

    # Authentication: override SECRET_KEY for every deployed instance.
    secret_key: str = Field(default=DEV_SECRET_KEY, min_length=1)
    access_token_expire_minutes: int = Field(default=1440, gt=0)

    # Same-origin frontend needs no CORS. Cookies are never enabled.
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value):
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

    cache_retention_days: int = Field(default=30, gt=0)

    # Cache TTL (seconds)
    cache_ttl_current_price: int = 60        # 1分
    cache_ttl_history: int = 86400           # 1日
    cache_ttl_financials: int = 7776000      # 約90日（四半期）
    cache_ttl_news: int = 600                # 10分

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # App
    app_name: str = "Stock Data API"
    app_version: str = "1.0.0"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    """設定のシングルトンインスタンスを取得"""
    return Settings()
