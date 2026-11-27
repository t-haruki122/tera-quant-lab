import unicodedata
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BeforeValidator
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.routers.dependencies import limit_read_data_requests
from app.schemas.symbols import SymbolSearchResponse
from app.services.fund_service import get_fund
from app.services.symbol_directory import directory, fund_match
from app.validation import FUND_CODE, FUND_PREFIX, fund_code

router = APIRouter(prefix="/symbols", tags=["symbols"], dependencies=[Depends(limit_read_data_requests)])


@router.get("/search")
async def search_symbols(
    q: Annotated[str, BeforeValidator(str.strip), Query(min_length=1, max_length=50)],
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
    db: AsyncSession = Depends(get_db),
) -> SymbolSearchResponse:
    """銘柄名、銘柄コード、ティッカーから株式・投資信託を検索します。"""
    query = unicodedata.normalize("NFKC", q).upper()
    code = fund_code(query)
    if code is None and FUND_CODE.fullmatch(query) and any(char.isdigit() for char in query):
        code = query
    if code is not None and FUND_CODE.fullmatch(code):
        data = await get_fund(FUND_PREFIX + code, db)
        return SymbolSearchResponse(items=[fund_match(code, data["name"])])
    return SymbolSearchResponse(items=await directory.search(q, limit))
