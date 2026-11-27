"""Asset exposure for the funds in the built-in search catalog."""

from dataclasses import dataclass

FUND_NAV_UNITS = 10_000
FUND_UNIT = "10,000口あたり"
FUND_SOURCE = "データ: 三菱UFJアセットマネジメント 投信情報API"


@dataclass(frozen=True)
class FundEntry:
    name: str
    aliases: tuple[str, ...]
    region: str
    exposure_currency: str


CATALOG = {
    "0331418A": FundEntry(
        "eMAXIS Slim 全世界株式(オール・カントリー)",
        ("オルカン", "全世界株式", "オール・カントリー", "eMAXIS Slim", "Slim"),
        "全世界株", "外貨（複数）",
    ),
    "03311187": FundEntry(
        "eMAXIS Slim 米国株式(S&P500)",
        ("S&P500", "S&P 500", "エスアンドピー", "米国株式", "eMAXIS Slim", "Slim"),
        "米国株", "USD",
    ),
    "03317172": FundEntry(
        "eMAXIS Slim 国内株式(TOPIX)",
        ("TOPIX", "国内株式", "eMAXIS Slim", "Slim"),
        "日本株", "JPY",
    ),
}
