from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

type Market = Literal["JP", "US"]


@dataclass(frozen=True, slots=True)
class SymbolMatch:
    symbol: str
    name: str
    market: Market
    segment: str | None
    sector: str | None
    is_etf: bool
    source: Literal["jpx", "nasdaqtrader", "profile", "yahoo", "fund"]


class SymbolSearchResponse(BaseModel):
    items: list[SymbolMatch]
