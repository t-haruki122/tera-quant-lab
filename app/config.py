"""アプリケーション設定管理"""

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """環境変数から読み込む設定"""

    # Database
    database_url: str = "sqlite+aiosqlite:///./stock_data.db"

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
