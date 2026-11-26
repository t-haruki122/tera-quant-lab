"""Stock Data API - メインアプリケーション"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import yfinance
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.routing import Route
from starlette.types import Scope

from app import database
from app.config import SECRET_KEY_MESSAGE, get_settings
from app.database import init_db
from app.exceptions import register_exception_handlers
from app.mcp_http import MCP_PATHS, AppCORSMiddleware, MCPGateway, serve_mcp
from app.middleware import InternalErrorMiddleware, RequestIDFilter, RequestMiddleware, TokenRedactingFormatter
from app.routers import api_tokens, backtest, forex, investments, stats, stock, symbols, user
from app.services.cache_warmer import warm_cache_periodically
from app.services.symbol_directory import initialize_symbol_directory
from app.stats import stats as app_stats


def setup_logging(log_level: str) -> None:
    """アプリ全体のログフォーマットを統一する。"""
    log_format = "%(asctime)s | %(levelname)s | %(name)s | request_id=%(request_id)s | %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"
    formatter = TokenRedactingFormatter(log_format, datefmt=date_format)

    level = logging.getLevelNamesMapping().get(log_level.upper(), 5)  # Uvicorn's TRACE is 5.
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(level=level)

    root_logger.setLevel(level)
    request_id_filter = RequestIDFilter()
    for handler in root_logger.handlers:
        handler.addFilter(request_id_filter)
        handler.setFormatter(formatter)
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        target_logger = logging.getLogger(logger_name)
        target_logger.setLevel(level)
        for handler in target_logger.handlers:
            handler.addFilter(request_id_filter)
            handler.setFormatter(formatter)


settings = get_settings()
setup_logging(settings.log_level)
logger = logging.getLogger(__name__)
started_at = time.monotonic()

# フロントエンドディレクトリ
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


frontend = StaticFiles(directory=FRONTEND_DIR) if FRONTEND_DIR.exists() else None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """アプリケーションのライフサイクル管理"""
    settings.validate_startup()
    if settings.has_weak_secret():
        logger.warning(SECRET_KEY_MESSAGE)
    if settings.app_env == "production" and "*" in settings.cors_origins:
        logger.warning('APP_ENV=production で CORS_ORIGINS に "*" が指定されています。許可先を限定してください。')
    if settings.yfinance_cache_dir:
        cache_dir = Path(settings.yfinance_cache_dir).expanduser().resolve()
        cache_dir.mkdir(parents=True, exist_ok=True)
        # yfinance 1.8 applies this setting to its timezone, cookie, and ISIN caches.
        yfinance.set_tz_cache_location(str(cache_dir))
    await init_db()
    symbol_task = asyncio.create_task(initialize_symbol_directory(settings))
    flush_task = asyncio.create_task(app_stats.flush_periodically(settings.stats_flush_seconds))
    warm_task = None
    if settings.cache_warm_enabled:
        warm_task = asyncio.create_task(warm_cache_periodically(
            settings.cache_warm_interval_minutes, settings.cache_warm_concurrency,
        ))
    logger.info("Application startup completed")
    try:
        async with serve_mcp(app, settings):
            yield
    finally:
        flush_task.cancel()
        symbol_task.cancel()
        if warm_task is not None:
            warm_task.cancel()
            with suppress(asyncio.CancelledError):
                await warm_task
        with suppress(asyncio.CancelledError):
            await flush_task
        with suppress(asyncio.CancelledError):
            await symbol_task
        await app_stats.flush()
        await database.engine.dispose()
        logger.info("Application shutdown completed")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="株価分析・アルゴリズム研究のためのREST API。株価、財務情報、ニュースを統合して提供します。",
    lifespan=lifespan,
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
    swagger_ui_oauth2_redirect_url="/docs/oauth2-redirect" if settings.docs_enabled else None,
)

# CORS設定
def configure_cors(app: FastAPI, origins: list[str], *, allow_credentials: bool = False) -> None:
    if "*" in origins and allow_credentials:
        raise ValueError("Wildcard CORS origins cannot be combined with credentials")
    if origins:
        app.add_middleware(
            AppCORSMiddleware,
            allow_origins=origins,
            allow_credentials=allow_credentials,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
            expose_headers=["X-Request-ID", "Retry-After"],
        )


def record_request(scope: Scope, duration: float, status_code: int) -> None:
    if scope["path"] not in {"/", "/health"} and not isinstance(scope.get("endpoint"), StaticFiles):
        endpoint = getattr(scope.get("route"), "path", "unmatched")
        app_stats.log_request(endpoint, duration, status_code)


app.add_middleware(InternalErrorMiddleware)
configure_cors(app, settings.cors_origins)
app.add_middleware(
    RequestMiddleware,
    record_request=record_request,
    docs_paths=frozenset(path for path in (app.docs_url, app.redoc_url, app.swagger_ui_oauth2_redirect_url) if path),
)

# ルーター登録
app.include_router(stock.router)
app.include_router(user.router)
app.include_router(api_tokens.router)
app.include_router(investments.router)
app.include_router(stats.router)
app.include_router(forex.router)
app.include_router(backtest.router)
app.include_router(symbols.router)
app.router.routes.extend(Route(path, MCPGateway(), name="mcp") for path in MCP_PATHS)

# 例外ハンドラー登録
register_exception_handlers(app)

# 静的ファイル配信（フロントエンド）
if frontend is not None:
    app.mount("/static", frontend, name="static")


@app.get("/health", tags=["root"], responses={503: {"description": "データベースに接続できません"}})
async def health():
    """データベース接続とマイグレーションの状態を返す。"""
    status, database_status, status_code, revision = "ok", "ok", 200, None
    try:
        revision = await database.check_health()
    except Exception:
        logger.exception("Database health check failed")
        status, database_status, status_code = "degraded", "error", 503
    return JSONResponse(status_code=status_code, content={
        "status": status,
        "version": settings.app_version,
        "uptime_seconds": int(time.monotonic() - started_at),
        "database": database_status,
        "migration": revision,
    })


@app.get("/", tags=["root"])
async def root(request: Request):
    """フロントエンドのindex.htmlを返す"""
    index_path = FRONTEND_DIR / "index.html"
    if frontend is not None and index_path.exists():
        return await frontend.get_response("index.html", request.scope)
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "status": "running",
    }
