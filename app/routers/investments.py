"""認証が必要な価格アラート API。"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_read_owner, require_user_owner
from app.config import get_settings
from app.database import get_db
from app.rate_limit import limiter
from app.schemas.investments import AlertCheckResponse, AlertResponse, AlertsResponse, AlertWrite
from app.services import price_alert_service
from app.validation import SQLiteID

router = APIRouter(prefix="/user/{user_id}", tags=["user"])
session_only = [Depends(require_user_owner)]


@router.get("/alerts", response_model=AlertsResponse, dependencies=[Depends(require_read_owner)],
            summary="価格アラート一覧を取得")
async def alerts(user_id: SQLiteID, db: AsyncSession = Depends(get_db)):
    return {"alerts": await price_alert_service.alerts_for_user(user_id, db)}


@router.post("/alerts", status_code=201, response_model=AlertResponse, dependencies=session_only,
             summary="価格アラートを追加")
async def add_alert(user_id: SQLiteID, data: AlertWrite, db: AsyncSession = Depends(get_db)):
    try:
        return await price_alert_service.add_alert(user_id, data, db)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/alerts/{alert_id}", response_model=AlertResponse, dependencies=session_only, summary="価格アラートを更新")
async def update_alert(user_id: SQLiteID, alert_id: SQLiteID, data: AlertWrite, db: AsyncSession = Depends(get_db)):
    try:
        return await price_alert_service.update_alert(user_id, alert_id, data, db)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.delete("/alerts/{alert_id}", status_code=204, dependencies=session_only, summary="価格アラートを削除")
async def delete_alert(user_id: SQLiteID, alert_id: SQLiteID, db: AsyncSession = Depends(get_db)):
    try:
        await price_alert_service.delete_alert(user_id, alert_id, db)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/alerts/check", response_model=AlertCheckResponse, dependencies=session_only,
             summary="有効な価格アラートを判定")
async def check_alerts(user_id: SQLiteID, request: Request, db: AsyncSession = Depends(get_db)):
    limiter.data(request, get_settings(), user_id=user_id)
    return await price_alert_service.check_alerts(user_id, db)
