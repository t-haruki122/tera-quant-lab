"""Stock Data API - メインアプリケーション"""

import time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.config import get_settings
from app.database import init_db
from app.routers import stock, user, stats, forex
from app.exceptions import register_exception_handlers
from app.stats import stats as app_stats

settings = get_settings()

# フロントエンドディレクトリ
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """アプリケーションのライフサイクル管理"""
    # 起動時: DB初期化
    await init_db()
    yield
    # 終了時: クリーンアップ（必要に応じて追加）


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="株価分析・アルゴリズム研究のためのREST API。株価、財務情報、ニュースを統合して提供します。",
    lifespan=lifespan,
)

# CORS設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def stats_middleware(request: Request, call_next):
    """リクエストの統計情報を記録するミドルウェア"""
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    
    path = request.url.path
    # 静的ファイル配信やルートへのアクセスはAPI統計から除外
    if not path.startswith("/static") and path != "/":
        app_stats.log_request(path, process_time, response.status_code)
        
    return response

# ルーター登録
app.include_router(stock.router)
app.include_router(user.router)
app.include_router(stats.router)
app.include_router(forex.router)

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
