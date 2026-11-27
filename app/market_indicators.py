"""経済指標タブに表示する銘柄と単位。"""

from dataclasses import dataclass
from typing import Literal

from app.validation import fund_code

type MarketUnit = Literal["points", "jpy", "usd", "usd_per_oz", "usd_per_barrel", "yield_pct"]
USD_JPY_SYMBOL = "JPY=X"


@dataclass(frozen=True)
class MarketIndicator:
    symbol: str
    name: str
    group: str
    unit: MarketUnit
    currency: str | None
    note: str | None = None


INDICATORS = (
    MarketIndicator("^N225", "日経平均", "日本", "points", "JPY"),
    MarketIndicator("1306.T", "TOPIX（1306.T ETFで代用）", "日本", "jpy", "JPY", "ETFで代用"),
    MarketIndicator("^GSPC", "S&P 500", "米国", "points", "USD"),
    MarketIndicator("^DJI", "NYダウ", "米国", "points", "USD"),
    MarketIndicator("^IXIC", "NASDAQ総合", "米国", "points", "USD"),
    MarketIndicator(USD_JPY_SYMBOL, "ドル円", "為替", "jpy", "JPY"),
    MarketIndicator("EURJPY=X", "ユーロ円", "為替", "jpy", "JPY"),
    MarketIndicator("GC=F", "金", "商品", "usd_per_oz", "USD", "先物"),
    MarketIndicator("CL=F", "WTI原油", "商品", "usd_per_barrel", "USD", "先物"),
    MarketIndicator("^TNX", "米10年国債利回り", "金利・リスク", "yield_pct", None),
    MarketIndicator("^VIX", "VIX", "金利・リスク", "points", None),
    MarketIndicator("BTC-USD", "ビットコイン", "暗号資産", "usd", "USD"),
)
BY_SYMBOL = {indicator.symbol: indicator for indicator in INDICATORS}


def is_non_company(symbol: str) -> bool:
    # The TOPIX proxy is an ETF. Its existing profile and distribution data remain available.
    return fund_code(symbol) is not None or symbol.startswith("^") or symbol.endswith(("=F", "=X")) or symbol == "BTC-USD"
