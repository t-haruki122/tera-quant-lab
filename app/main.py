"""Stock Data API - メインアプリケーション"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import database
from app.config import DEV_SECRET_KEY, get_settings
from app.database import init_db
from app.exceptions import register_exception_handlers
from app.routers import backtest, forex, stats, stock, user
from app.stats import stats as app_stats


def setup_logging() -> None:
    """アプリ全体のログフォーマットを統一する。"""
    log_format = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(log_format, datefmt=date_format)

    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(level=logging.INFO, format=log_format, datefmt=date_format)
    else:
        root_logger.setLevel(logging.INFO)
        for handler in root_logger.handlers:
            handler.setFormatter(formatter)

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        target_logger = logging.getLogger(logger_name)
        target_logger.setLevel(logging.INFO)
        for handler in target_logger.handlers:
            handler.setFormatter(formatter)


setup_logging()
logger = logging.getLogger("app.main")

settings = get_settings()

# フロントエンドディレクトリ
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """アプリケーションのライフサイクル管理"""
    # 起動時: DB初期化
    if settings.secret_key == DEV_SECRET_KEY:
        logger.warning(
            "SECRET_KEY is using the insecure development default. "
            "Set a strong, private SECRET_KEY before deploying; tokens can otherwise be forged."
        )
    await init_db()
    flush_task = asyncio.create_task(app_stats.flush_periodically(settings.stats_flush_seconds))
    logger.info("Application startup completed")
    try:
        yield
    finally:
        flush_task.cancel()
        with suppress(asyncio.CancelledError):
            await flush_task
        await app_stats.flush()
        await database.engine.dispose()
        logger.info("Application shutdown completed")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="株価分析・アルゴリズム研究のためのREST API。株価、財務情報、ニュースを統合して提供します。",
    lifespan=lifespan,
)

# CORS設定
def configure_cors(app: FastAPI, origins: list[str], *, allow_credentials: bool = False) -> None:
    if "*" in origins and allow_credentials:
        raise ValueError("Wildcard CORS origins cannot be combined with credentials")
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=allow_credentials,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )


configure_cors(app, settings.cors_origins)

@app.middleware("http")
async def stats_middleware(request: Request, call_next):
    """リクエストの統計情報を記録するミドルウェア"""
    start_time = time.perf_counter()
    response = await call_next(request)
    process_time = time.perf_counter() - start_time

    path = request.url.path
    if not isinstance(request.scope.get("endpoint"), StaticFiles):
        endpoint = getattr(request.scope.get("route"), "path", "unmatched")
        app_stats.log_request(endpoint, process_time, response.status_code)

    logger.info(
        "HTTP %s %s -> %s (%.2f ms)",
        request.method,
        path,
        response.status_code,
        process_time * 1000,
    )

    return response

# ルーター登録
app.include_router(stock.router)
app.include_router(user.router)
app.include_router(stats.router)
app.include_router(forex.router)
app.include_router(backtest.router)

# 例外ハンドラー登録
register_exception_handlers(app)

# 静的ファイル配信（フロントエンド）
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/", tags=["root"])
async def root():
    """フロントエンドのindex.htmlを返す"""
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "status": "running",
    }
