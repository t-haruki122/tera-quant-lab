"""株価データ APIルーター"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BeforeValidator
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.routers.dependencies import limit_data_requests, limit_read_data_requests
from app.schemas.stock import (
    CompanyProfileResponse,
    DividendHistoryResponse,
    FinancialHistoryResponse,
    FinancialResponse,
    IndicatorsResponse,
    NewsResponse,
    StockHistoryResponse,
    StockPriceResponse,
    StockSnapshot,
)
from app.services import (
    finance_service,
    indicator_service,
    news_service,
    profile_service,
    stock_service,
    watchlist_service,
)
from app.timeutil import utcnow
from app.validation import HistoryInterval, ISODate, Symbol, shift_years

router = APIRouter(prefix="/stock", tags=["stock"])


@router.get(
    "/{symbol}",
    dependencies=[Depends(limit_read_data_requests)],
    response_model=StockPriceResponse,
    summary="現在の株価を取得",
    description="指定されたシンボルの現在の株価を返します。",
)
async def get_stock_price(symbol: Symbol, db: AsyncSession = Depends(get_db)):
    """現在の株価を取得"""
    return await stock_service.get_current_price(symbol, db)


@router.get("/{symbol}/snapshot", dependencies=[Depends(limit_data_requests)], response_model=StockSnapshot, response_model_exclude_unset=True,
            summary="銘柄の株価・会社情報・投資指数を取得")
async def get_stock_snapshot(symbol: Symbol, db: AsyncSession = Depends(get_db)):
    return await watchlist_service.get_snapshot(symbol, db.bind)


@router.get(
    "/{symbol}/history",
    dependencies=[Depends(limit_read_data_requests)],
    response_model=StockHistoryResponse,
    summary="過去の株価データを取得",
    description="指定された期間の過去株価データを返します。",
)
async def get_stock_history(
    symbol: Symbol,
    start_date: Annotated[ISODate | None, Query(description="開始日 (YYYY-MM-DD)")] = None,
    end_date: Annotated[ISODate | None, Query(description="終了日 (YYYY-MM-DD)")] = None,
    interval: Annotated[HistoryInterval, BeforeValidator(str.lower), Query(description="データ間隔 (1d, 1wk, 1mo)")] = "1d",
    db: AsyncSession = Depends(get_db),
):
    """過去の株価データを取得"""
    if start_date is not None:
        end = end_date or utcnow().date()
        if start_date > end:
            raise HTTPException(422, "開始日（start_date）は終了日（end_date）以前にしてください")
        years = get_settings().history_max_span_years
        if end > shift_years(start_date, years):
            raise HTTPException(422, f"株価履歴の取得期間は最大 {years} 年です")

    return await stock_service.get_history(
        symbol, db, start_date.isoformat() if start_date else None, end_date.isoformat() if end_date else None, interval,
    )


@router.get(
    "/{symbol}/financials",
    dependencies=[Depends(limit_read_data_requests)],
    response_model=FinancialResponse,
    summary="財務情報を取得",
    description="指定されたシンボルの財務情報（売上、純利益、EPS、PE比率）を返します。",
)
async def get_financials(symbol: Symbol, db: AsyncSession = Depends(get_db)):
    """財務情報を取得"""
    return await finance_service.get_financials(symbol, db)


@router.get(
    "/{symbol}/financials/history",
    dependencies=[Depends(limit_read_data_requests)],
    response_model=FinancialHistoryResponse,
    summary="過去の財務情報を取得",
    description="指定されたシンボルの年次財務履歴（売上高・純利益）を返します。",
)
async def get_financial_history(
    symbol: Symbol,
    limit: int = Query(6, ge=1, le=10, description="取得する年次データ件数"),
    db: AsyncSession = Depends(get_db),
):
    """過去の財務情報を取得"""
    return await finance_service.get_financial_history(symbol, db, limit=limit)


@router.get(
    "/{symbol}/dividends/history",
    dependencies=[Depends(limit_data_requests)],
    response_model=DividendHistoryResponse,
    summary="過去の配当情報を取得",
    description="指定されたシンボルの配当履歴（1株配当・年利換算の配当利回り）を返します。",
)
async def get_dividend_history(
    symbol: Symbol,
    limit: int = Query(20, ge=1, le=60, description="取得する配当履歴件数"),
    db: AsyncSession = Depends(get_db),
):
    """過去の配当情報を取得"""
    return await finance_service.get_dividend_history(symbol, db, limit=limit)


@router.get(
    "/{symbol}/news",
    dependencies=[Depends(limit_read_data_requests)],
    response_model=NewsResponse,
    summary="関連ニュースを取得",
    description="指定されたシンボルに関連するニュースを返します。",
)
async def get_news(symbol: Symbol, db: AsyncSession = Depends(get_db)):
    """関連ニュースを取得"""
    return await news_service.get_news(symbol, db)


@router.get(
    "/{symbol}/profile",
    dependencies=[Depends(limit_read_data_requests)],
    response_model=CompanyProfileResponse,
    summary="会社プロフィールを取得",
    description="指定されたシンボルの会社情報（セクター、業種、時価総額等）を返します。",
)
async def get_profile(symbol: Symbol, db: AsyncSession = Depends(get_db)):
    """会社プロフィールを取得"""
    return await profile_service.get_profile(symbol, db)


@router.get(
    "/{symbol}/indicators",
    dependencies=[Depends(limit_read_data_requests)],
    response_model=IndicatorsResponse,
    summary="投資指数を取得",
    description="指定されたシンボルの投資指数（ROE、ROA、PBR、PER、ミックス指数等）を返します。",
)
async def get_indicators(symbol: Symbol, db: AsyncSession = Depends(get_db)):
    """投資指数を取得"""
    return await indicator_service.get_indicators(symbol, db)
