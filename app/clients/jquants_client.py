"""Sequential J-Quants V2 requests with a shared sliding-window limit."""

import asyncio
import re
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import date, time
from decimal import Decimal, InvalidOperation
from math import isfinite
from time import monotonic
from typing import Any

import httpx

from app.config import Settings

type Record = dict[str, Any]
type BeforeRequest = Callable[[], Awaitable[None]]


class JQuantsError(Exception):
    """Contains only application-defined messages safe for logs and status."""


class JQuantsRequestError(JQuantsError):
    def __init__(self, message: str, *, outside_window: bool = False):
        super().__init__(message)
        self.outside_window = outside_window


class JQuantsAuthError(JQuantsRequestError):
    """The key or plan does not allow the request."""


class RequestLimiter:
    def __init__(
        self, *, clock: Callable[[], float] = monotonic, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._requests: deque[float] = deque()

    @asynccontextmanager
    async def slot(self, maximum: int, before_request: BeforeRequest | None):
        async with self._lock:
            while True:
                now = self._clock()
                while self._requests and now - self._requests[0] > 60:
                    self._requests.popleft()
                if len(self._requests) < maximum:
                    break
                await self._sleep(self._requests[0] + 60.001 - now)
            if before_request is not None:
                await before_request()
            self._requests.append(self._clock())
            yield


_limiter = RequestLimiter()
_ATTEMPTS = 3
_DATE_WINDOW = re.compile(
    r"outside (?:of )?(?:the )?(?:available |allowed |supported )?(?:data |date )?(?:window|range|period)"
    r"|(?:not available|not accessible).*(?:specified|requested|selected) date"
    r"|(?:date|data).*(?:available|accessible|provided).*(?:period|range|\d{4}[-/]?\d{2}[-/]?\d{2})"
    r"|(?:date|data).*(?:out of range|too old|too recent)"
    r"|(?:date|data).*(?:within|between|on or after|on or before).*(?:range|period|\d{4}[-/]?\d{2}[-/]?\d{2})"
    r"|(?:日付|データ|指定日).*(?:範囲外|期間外|期間内|取得可能期間)",
    re.IGNORECASE,
)


def _outside_window(response: httpx.Response) -> bool:
    # Inspect the provider message for explicit date restrictions, but never retain or expose it.
    try:
        payload = response.json()
    except ValueError:
        return False
    message = payload.get("message") if isinstance(payload, dict) else None
    return isinstance(message, str) and bool(_DATE_WINDOW.search(message))


class JQuantsClient:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None,
        limiter: RequestLimiter = _limiter, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        before_request: BeforeRequest | None = None,
    ):
        if not settings.jquants_active or settings.jquants_api_key is None:
            raise JQuantsError("J-Quants が無効です")
        self._maximum = settings.jquants_requests_per_minute
        self._limiter = limiter
        self._sleep = sleep
        self._before_request = before_request
        self._key = settings.jquants_api_key.get_secret_value()
        try:
            self._http = httpx.AsyncClient(
                base_url=settings.jquants_base_url.rstrip("/") + "/", timeout=20, transport=transport,
                headers={"x-api-key": self._key, "User-Agent": "tera-quant-lab"},
                follow_redirects=False, trust_env=False,
            )
        except (httpx.InvalidURL, ValueError):
            pass
        else:
            return
        raise JQuantsError("J-Quants の接続設定が無効です")

    async def __aenter__(self) -> "JQuantsClient":
        return self

    async def __aexit__(self, *_):
        await self._http.aclose()

    async def _request(self, path: str, params: dict[str, str]) -> httpx.Response:
        for attempt in range(_ATTEMPTS):
            delay = 2**attempt
            async with self._limiter.slot(self._maximum, self._before_request):
                try:
                    async with asyncio.timeout(30):
                        response = await self._http.get(path, params=params)
                except (httpx.RequestError, TimeoutError):
                    error = JQuantsError("J-Quants に接続できませんでした")
                else:
                    status = response.status_code
                    if status == 200:
                        return response
                    if status in (400, 403):
                        outside_window = path == "fins/summary" and _outside_window(response)
                        if status == 403:
                            raise JQuantsAuthError(
                                "J-Quants の API キーまたは契約プランを確認してください (403)",
                                outside_window=outside_window,
                            )
                        raise JQuantsRequestError("J-Quants がリクエストを拒否しました (400)", outside_window=outside_window)
                    if status == 429:
                        delay = 120
                        error = JQuantsError("J-Quants のリクエスト上限に達しました (429)")
                    elif 500 <= status < 600:
                        error = JQuantsError(f"J-Quants でサーバーエラーが発生しました ({status})")
                    else:
                        raise JQuantsError(f"J-Quants が予期しないステータスを返しました ({status})")
            if attempt < _ATTEMPTS - 1:
                await self._sleep(delay)
            else:
                raise error
        raise AssertionError("at least one request attempt is required")

    async def _pages(self, path: str, params: dict[str, str]) -> list[Record]:
        rows: list[Record] = []
        seen: set[str] = set()
        while True:
            response = await self._request(path, params)
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if not isinstance(payload, dict):
                raise JQuantsError("J-Quants の応答形式が不正です")
            data = payload.get("data")
            if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
                raise JQuantsError("J-Quants の応答形式が不正です")
            if any(self._key in value for row in data for value in row.values() if isinstance(value, str)):
                raise JQuantsError("J-Quants の応答に認証情報が含まれていました")
            rows.extend(data)
            cursor = payload.get("pagination_key")
            if not cursor and path == "fins/summary":
                cursor = payload.get("cursor")
            if cursor in (None, ""):
                return rows
            if not isinstance(cursor, str) or cursor in seen or self._key in cursor:
                raise JQuantsError("J-Quants のページ情報が不正です")
            seen.add(cursor)
            params = {**params, "pagination_key": cursor}

    async def master(self) -> list[Record]:
        return await self._pages("equities/master", {})

    async def summaries(self, day: date) -> list[Record]:
        return await self._pages("fins/summary", {"date": day.isoformat()})

_MASTER_FIELDS = {
    "name": "CoName",
    "name_en": "CoNameEn",
    "sector17_code": "S17",
    "sector17_name": "S17Nm",
    "sector33_code": "S33",
    "sector33_name": "S33Nm",
    "scale_category": "ScaleCat",
    "market_code": "Mkt",
    "market_name": "MktNm",
}

_PERIOD_DATES = {
    "period_start": "CurPerSt",
    "period_end": "CurPerEn",
    "fy_start": "CurFYSt",
    "fy_end": "CurFYEn",
}

_AMOUNTS = {
    "sales": "Sales",
    "operating_profit": "OP",
    "ordinary_profit": "OdP",
    "net_profit": "NP",
    "eps": "EPS",
    "diluted_eps": "DEPS",
    "total_assets": "TA",
    "equity": "Eq",
    "equity_ratio": "EqAR",
    "bps": "BPS",
    "shareholders_equity": "ShEq",
    "roe": "ROE",
    "cfo": "CFO",
    "cfi": "CFI",
    "cff": "CFF",
    "cash": "CashEq",
    "div_annual": "DivAnn",
    "payout_ratio_annual": "PayoutRatioAnn",
    "forecast_sales": "FSales",
    "forecast_operating_profit": "FOP",
    "forecast_ordinary_profit": "FOdP",
    "forecast_net_profit": "FNP",
    "forecast_eps": "FEPS",
    "forecast_div_annual": "FDivAnn",
    "next_forecast_sales": "NxFSales",
    "next_forecast_operating_profit": "NxFOP",
    "next_forecast_ordinary_profit": "NxFOdP",
    "next_forecast_net_profit": "NxFNp",
    "next_forecast_eps": "NxFEPS",
    "next_forecast_div_annual": "NxFDivAnn",
}

_SHARES = {
    "shares_outstanding_fy": "ShOutFY",
    "treasury_shares_fy": "TrShFY",
    "average_shares": "AvgSh",
}


def _field(row: Record, name: str) -> str | None:
    value = row.get(name)
    return value.strip() or None if isinstance(value, str) else None


def _code(row: Record) -> str:
    code = _field(row, "Code")
    if code is None or not re.fullmatch(r"[A-Z0-9]{5}", code):
        raise JQuantsError("J-Quants の銘柄コードの形式が不正です")
    return code


def _symbol(code: str) -> str | None:
    return f"{code[:4]}.T" if code[4] == "0" else None


def _date(row: Record, name: str) -> date | None:
    value = _field(row, name)
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        pass
    raise JQuantsError("J-Quants の日付の形式が不正です")


def _time(value: str | None) -> time | None:
    if value is None:
        return None
    try:
        return time.fromisoformat(value)
    except ValueError:
        pass
    raise JQuantsError("J-Quants の時刻の形式が不正です")


def _number(value: str | None, *, integer: bool = False) -> float | int | None:
    if value is None:
        return None
    try:
        if integer:
            number = Decimal(value)
            if number.is_finite() and -(2**63) <= number < 2**63 and number == number.to_integral_value():
                return int(number)
        else:
            amount = float(value)
            if isfinite(amount):
                return amount
    except (ValueError, InvalidOperation):
        pass
    raise JQuantsError("J-Quants の数値の形式が不正です")


def parse_master(row: Record) -> Record:
    code = _code(row)
    as_of = _date(row, "Date")
    if as_of is None:
        raise JQuantsError("J-Quants の銘柄マスターに基準日がありません")
    return {
        "code5": code, "symbol": _symbol(code), "as_of_date": as_of,
        **{column: _field(row, field) for column, field in _MASTER_FIELDS.items()},
    }


def parse_summary(row: Record) -> Record:
    code = _code(row)
    disc_no = _field(row, "DiscNo")
    disclosed_date = _date(row, "DiscDate")
    if not disc_no or disclosed_date is None:
        raise JQuantsError("J-Quants の決算短信に開示番号または開示日がありません")
    doc_type = _field(row, "DocType")
    consolidated = None
    if doc_type and "NonConsolidated" in doc_type:
        consolidated = False
    elif doc_type and "Consolidated" in doc_type:
        consolidated = True

    def amount(field: str) -> float | int | None:
        value = _field(row, field)
        if value is None and field == "NxFNp":
            value = _field(row, "NxFNP")
        if value is None and consolidated is False:
            value = _field(row, f"NC{field}")
        return _number(value)

    return {
        "disc_no": disc_no, "code5": code, "symbol": _symbol(code), "disclosed_date": disclosed_date,
        "disclosed_time": _time(_field(row, "DiscTime")), "doc_type": doc_type,
        "period_type": _field(row, "CurPerType"), "consolidated": consolidated,
        **{column: _date(row, field) for column, field in _PERIOD_DATES.items()},
        **{column: amount(field) for column, field in _AMOUNTS.items()},
        **{column: _number(_field(row, field), integer=True) for column, field in _SHARES.items()},
    }
