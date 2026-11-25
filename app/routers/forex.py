"""為替データ APIルーター"""

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.routers.dependencies import limit_data_requests
from app.services import forex_service

router = APIRouter(prefix="/forex", tags=["forex"], dependencies=[Depends(limit_data_requests)])

class ExchangeRateResponse(BaseModel):
    pair: str
    rate: float
    timestamp: str

@router.get(
    "/{pair}",
    response_model=ExchangeRateResponse,
    summary="為替レートを取得",
    description="指定された為替ペア（例: USDJPY=X）の現在のレートを返します。",
)
async def get_exchange_rate(pair: str):
    """現在の為替レートを取得"""
    match = re.fullmatch(r"([A-Za-z]{6})(?:=[Xx])?", pair.strip())
    if match is None:
        raise HTTPException(422, "為替ペアは USDJPY または USDJPY=X のように英字6文字で指定してください")
    return await forex_service.get_exchange_rate(f"{match[1].upper()}=X")
