"""Client for MUFG Asset Management's latest fund information."""

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import httpx

from app.exceptions import ExternalAPIError, RateLimitError, SymbolNotFoundError, UpstreamUnavailableError
from app.validation import FUND_CODE, FUND_PREFIX

LATEST_URL = "https://developer.am.mufg.jp/fund_information_latest/association_fund_cd"
RETURN_PERIODS = ("1m", "3m", "6m", "1y", "3y", "full")
RISK_PERIODS = ("1y", "3y", "full")
EXTREME_PERIODS = ("1m", "3m", "6m", *RISK_PERIODS)


class UnsupportedFundError(SymbolNotFoundError):
    def __init__(self, code: str):
        self.symbol = FUND_PREFIX + code
        self.message = "このファンドは未対応です（三菱UFJアセットマネジメントのファンドのみ対応）"
        Exception.__init__(self, self.message)


@dataclass(frozen=True)
class FundInfo:
    association_code: str
    base_date: date
    nav: float
    cancellation_price: float | None
    net_assets: int | None
    change: float | None
    change_pct: float | None
    fund_name: str
    fund_cd: str | None
    isin: str | None
    stats: dict[str, Any]


def _number(value: object) -> float | None:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _text(value: object) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


def _date(value: object) -> date | None:
    if isinstance(value, str) and re.fullmatch(r"[0-9]{8}", value):
        try:
            return datetime.strptime(value, "%Y%m%d").date()
        except ValueError:
            pass
    return None


def parse_latest(code: str, payload: object) -> FundInfo:
    if not isinstance(payload, dict) or not isinstance(result := payload.get("result"), dict):
        raise ExternalAPIError()
    status = str(result.get("status"))
    rows = payload.get("datasets")
    if status == "404" or (status == "200" and (rows == [] or (rows is None and str(result.get("retcount")) == "0"))):
        raise UnsupportedFundError(code)
    if status != "200" or not isinstance(rows, list) or len(rows) != 1 or not isinstance(row := rows[0], dict):
        raise ExternalAPIError()
    base_date = _date(row.get("base_date"))
    nav = _number(row.get("nav"))
    name = _text(row.get("fund_name"))
    if row.get("association_fund_cd") != code or base_date is None or nav is None or nav <= 0 or name is None:
        raise ExternalAPIError()

    def extrema(kind: str) -> dict:
        return {period: {
            "nav": _number(row.get(f"nav_{kind}_{period}")),
            "date": day.isoformat() if (day := _date(row.get(f"nav_{kind}_{period}_dt"))) else None,
        } for period in EXTREME_PERIODS}

    assets = row.get("netassets")
    net_assets = int(assets) if (
        type(assets) is int and 0 <= assets <= 2**63 - 1
        or isinstance(assets, str) and re.fullmatch(r"[0-9]{1,19}", assets) and int(assets) <= 2**63 - 1
    ) else None
    return FundInfo(
        code, base_date, nav, _number(row.get("cancellation_price")), net_assets,
        _number(row.get("cmp_prev_day")), _number(row.get("percentage_change")),
        unicodedata.normalize("NFKC", name), _text(row.get("fund_cd")), _text(row.get("isin_cd")),
        {
            "returns": {period: _number(row.get(f"percentage_change_{period}")) for period in RETURN_PERIODS},
            "risk": {period: _number(row.get(f"risk_{period}")) for period in RISK_PERIODS},
            "risk_return": {period: _number(row.get(f"risk_return_{period}")) for period in RISK_PERIODS},
            "max_nav": extrema("max"),
            "min_nav": extrema("min"),
        },
    )


async def fetch_latest(code: str, *, transport: httpx.AsyncBaseTransport | None = None) -> FundInfo:
    if not FUND_CODE.fullmatch(code):
        raise UnsupportedFundError(code)
    try:
        async with httpx.AsyncClient(timeout=5, headers={"User-Agent": "tera-quant-lab/1.0"}, transport=transport) as client:
            response = await client.get(f"{LATEST_URL}/{code}")
            if response.status_code == 404:
                raise UnsupportedFundError(code)
            if response.status_code == 429:
                raise RateLimitError()
            response.raise_for_status()
            return parse_latest(code, response.json())
    except httpx.TransportError as exc:
        raise UpstreamUnavailableError() from exc
    except (httpx.HTTPStatusError, ValueError) as exc:
        raise ExternalAPIError() from exc
