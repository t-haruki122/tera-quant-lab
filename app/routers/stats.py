"""システム統計データのAPIルーター"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.services.jquants_sync import sync_status
from app.stats import stats

router = APIRouter(prefix="/stats", tags=["System Stats"])

@router.get("")
async def get_system_stats(db: AsyncSession = Depends(get_db)):
    """現在のAPI呼び出し回数とキャッシュヒット数を取得"""
    result = {**await stats.daily_stats(), **stats.get_stats()}
    settings = get_settings()
    if settings.jquants_api_key:
        result["jquants"] = await sync_status(db, settings, counts=False)
    return result
