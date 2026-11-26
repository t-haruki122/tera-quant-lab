"""認証が必要な保有銘柄・価格アラート API。"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_user_owner
from app.config import get_settings
from app.database import get_db
from app.rate_limit import limiter
from app.schemas.investments import (
    AlertCheckResponse,
    AlertResponse,
    AlertsResponse,
    AlertWrite,
    HoldingCreate,
    HoldingResponse,
    HoldingUpdate,
    PortfolioResponse,
)
from app.services import portfolio_service, price_alert_service, user_backup_service
from app.timeutil import utcnow
from app.validation import SQLiteID

router = APIRouter(prefix="/user/{user_id}", tags=["user"], dependencies=[Depends(require_user_owner)])


@router.get("/portfolio", response_model=PortfolioResponse, summary="ポートフォリオの評価額を取得")
async def portfolio(user_id: SQLiteID, request: Request, db: AsyncSession = Depends(get_db)):
    limiter.data(request, get_settings(), user_id=user_id)
    return await portfolio_service.summarize(user_id, db)


@router.get("/portfolio/export.csv", summary="保有銘柄をCSVで出力")
async def portfolio_csv(user_id: SQLiteID, db: AsyncSession = Depends(get_db)):
    return Response(
        await user_backup_service.export_portfolio_csv(user_id, db), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="portfolio-{utcnow().date()}.csv"'},
    )


@router.post("/portfolio", status_code=201, response_model=HoldingResponse, summary="保有銘柄を追加")
async def add_holding(user_id: SQLiteID, data: HoldingCreate, db: AsyncSession = Depends(get_db)):
    try:
        return await portfolio_service.add_holding(user_id, data, db)
    except portfolio_service.DuplicateHolding as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/portfolio/{holding_id}", response_model=HoldingResponse, summary="保有銘柄を更新")
async def update_holding(user_id: SQLiteID, holding_id: SQLiteID, data: HoldingUpdate, db: AsyncSession = Depends(get_db)):
    try:
        return await portfolio_service.update_holding(user_id, holding_id, data, db)
    except portfolio_service.DuplicateHolding as exc:
        raise HTTPException(409, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.delete("/portfolio/{holding_id}", status_code=204, summary="保有銘柄を削除")
async def delete_holding(user_id: SQLiteID, holding_id: SQLiteID, db: AsyncSession = Depends(get_db)):
    try:
        await portfolio_service.delete_holding(user_id, holding_id, db)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/alerts", response_model=AlertsResponse, summary="価格アラート一覧を取得")
async def alerts(user_id: SQLiteID, db: AsyncSession = Depends(get_db)):
    return {"alerts": await price_alert_service.alerts_for_user(user_id, db)}


@router.post("/alerts", status_code=201, response_model=AlertResponse, summary="価格アラートを追加")
async def add_alert(user_id: SQLiteID, data: AlertWrite, db: AsyncSession = Depends(get_db)):
    try:
        return await price_alert_service.add_alert(user_id, data, db)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/alerts/{alert_id}", response_model=AlertResponse, summary="価格アラートを更新")
async def update_alert(user_id: SQLiteID, alert_id: SQLiteID, data: AlertWrite, db: AsyncSession = Depends(get_db)):
    try:
        return await price_alert_service.update_alert(user_id, alert_id, data, db)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.delete("/alerts/{alert_id}", status_code=204, summary="価格アラートを削除")
async def delete_alert(user_id: SQLiteID, alert_id: SQLiteID, db: AsyncSession = Depends(get_db)):
    try:
        await price_alert_service.delete_alert(user_id, alert_id, db)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/alerts/check", response_model=AlertCheckResponse, summary="有効な価格アラートを判定")
async def check_alerts(user_id: SQLiteID, request: Request, db: AsyncSession = Depends(get_db)):
    limiter.data(request, get_settings(), user_id=user_id)
    return await price_alert_service.check_alerts(user_id, db)
