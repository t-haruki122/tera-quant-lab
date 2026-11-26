from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BeforeValidator

from app.routers.dependencies import limit_read_data_requests
from app.schemas.symbols import SymbolSearchResponse
from app.services.symbol_directory import directory

router = APIRouter(prefix="/symbols", tags=["symbols"], dependencies=[Depends(limit_read_data_requests)])


@router.get("/search")
async def search_symbols(
    q: Annotated[str, BeforeValidator(str.strip), Query(min_length=1, max_length=50)],
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
) -> SymbolSearchResponse:
    """銘柄名、銘柄コード、ティッカーから日本株・米国株を検索します。"""
    return SymbolSearchResponse(items=await directory.search(q, limit))
