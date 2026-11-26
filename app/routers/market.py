"""経済指標 APIルーター"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.routers.dependencies import limit_read_data_requests
from app.schemas.market import MarketOverviewResponse
from app.services.market_service import get_market_overview

router = APIRouter(prefix="/market", tags=["market"], dependencies=[Depends(limit_read_data_requests)])


@router.get(
    "/overview", response_model=MarketOverviewResponse, response_model_exclude_unset=True,
    summary="主要な経済指標と日次終値の推移を取得",
)
async def overview(cached: bool = False, db: AsyncSession = Depends(get_db)):
    return await get_market_overview(db, cached=cached)
