"""カスタム例外・グローバルエラーハンドラー"""

import logging
import math

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

INTERNAL_ERROR = "サーバー内部でエラーが発生しました。しばらくしてから再試行してください。"
UPSTREAM_ERROR = "データ提供元からデータを取得できませんでした。しばらくしてから再試行してください。"
UPSTREAM_UNAVAILABLE = "データ提供元に接続できませんでした。しばらくしてから再試行してください。"
UPSTREAM_RATE_LIMIT = "データ提供元の利用上限に達しました。しばらくしてから再試行してください。"
HTTP_DETAILS_JA = {"Not Found": "リクエストされたリソースが見つかりません", "Method Not Allowed": "この操作は許可されていません"}
VALIDATION_LIMITS = {
    "greater_than": ("gt", "より大きい"),
    "greater_than_equal": ("ge", "以上の"),
    "less_than": ("lt", "未満の"),
    "less_than_equal": ("le", "以下の"),
}
VALIDATION_TYPES_JA = {
    "dict_type": "オブジェクト",
    "list_type": "配列",
    "string_type": "文字列",
    "float_parsing": "数値",
    "float_type": "数値",
    "int_parsing": "整数",
    "int_type": "整数",
    "finite_number": "有限の数値",
}
logger = logging.getLogger(__name__)


def internal_error_response() -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": INTERNAL_ERROR})


def upstream_error_response(
    request: Request, error: Exception, status_code: int, detail: str, *, headers: dict[str, str] | None = None,
) -> JSONResponse:
    logger.warning("Upstream request failed (%d): %s", status_code, error,
                   extra={"request_id": getattr(request.state, "request_id", "-")})
    return JSONResponse(status_code=status_code, content={"detail": detail, "status_code": status_code}, headers=headers)


class SymbolNotFoundError(Exception):
    """シンボルが見つからない場合 (404)"""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.message = f"銘柄が見つかりません: {symbol}"
        super().__init__(self.message)


class RateLimitError(Exception):
    """APIレート制限 (429)"""

    def __init__(self, message: str = UPSTREAM_RATE_LIMIT):
        self.message = message
        super().__init__(self.message)


class ExternalAPIError(Exception):
    """外部APIエラー (502)"""

    def __init__(self, message: str = UPSTREAM_ERROR, *, allow_stale: bool = True):
        self.message = message
        self.allow_stale = allow_stale
        super().__init__(self.message)


class UpstreamUnavailableError(ExternalAPIError):
    """Network failures and timeouts (503)."""

    def __init__(self, message: str = UPSTREAM_UNAVAILABLE, *, allow_stale: bool = True):
        super().__init__(message, allow_stale=allow_stale)


def json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value


def register_exception_handlers(app: FastAPI):
    """FastAPIに例外ハンドラーを登録"""

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        errors = []
        for error in exc.errors():
            field = error["loc"][-1] if error["loc"] else None
            kind = error["type"]
            context = error.get("ctx", {})
            if kind == "value_error":
                message = error["msg"].removeprefix("Value error, ")
            elif field in {"start_date", "end_date"}:
                message = f"{field} は YYYY-MM-DD 形式の実在する日付を指定してください"
            elif field == "interval":
                message = "interval は 1d、1wk、1mo のいずれかを指定してください"
            elif field == "limit":
                bound = context.get("ge") or context.get("le")
                operator = "以上" if kind == "greater_than_equal" else "以下"
                message = f"limit は {bound} {operator}の整数を指定してください" if bound else "limit は整数を指定してください"
            elif field in {"user_id", "list_id"}:
                message = f"{field} は 1 以上の有効な整数 ID を指定してください"
            elif field == "symbol":
                message = "symbol は銘柄を表す文字列を指定してください"
            elif kind in VALIDATION_LIMITS:
                key, comparison = VALIDATION_LIMITS[kind]
                bound = str(context[key]).removesuffix(".0")
                message = f"{field} は {bound} {comparison}数値を指定してください"
            elif kind in VALIDATION_TYPES_JA:
                message = f"{field} は{VALIDATION_TYPES_JA[kind]}を指定してください"
            elif kind == "missing":
                message = f"{field} は必須です"
            elif kind == "json_invalid":
                message = "JSON の形式が正しくありません"
            elif kind == "literal_error":
                choices = context["expected"].replace(" or ", " または ")
                message = f"{field} は次のいずれかを指定してください: {choices}"
            else:
                message = f"{field} の値が正しくありません" if isinstance(field, str) else "入力内容が正しくありません"
            errors.append({"type": kind, "loc": error["loc"], "msg": message, "input": error.get("input")})
        return JSONResponse(status_code=422, content={"detail": json_safe(jsonable_encoder(errors))})

    @app.exception_handler(HTTPException)
    async def handled_http_exception(request: Request, exc: HTTPException):
        if exc.status_code == 500:
            # Route this through the same logging and response path as other internal errors.
            raise exc
        if isinstance(exc.detail, str) and exc.detail in HTTP_DETAILS_JA:
            exc = HTTPException(exc.status_code, HTTP_DETAILS_JA[exc.detail], headers=exc.headers)
        return await http_exception_handler(request, exc)

    @app.exception_handler(SymbolNotFoundError)
    async def symbol_not_found_handler(request: Request, exc: SymbolNotFoundError):
        return JSONResponse(
            status_code=404,
            content={"detail": exc.message, "status_code": 404},
        )

    @app.exception_handler(RateLimitError)
    async def rate_limit_handler(request: Request, exc: RateLimitError):
        return upstream_error_response(request, exc, 429, UPSTREAM_RATE_LIMIT, headers={"Retry-After": "60"})

    @app.exception_handler(UpstreamUnavailableError)
    async def unavailable_handler(request: Request, exc: UpstreamUnavailableError):
        return upstream_error_response(request, exc, 503, UPSTREAM_UNAVAILABLE)

    @app.exception_handler(ExternalAPIError)
    async def external_api_handler(request: Request, exc: ExternalAPIError):
        return upstream_error_response(request, exc, 502, UPSTREAM_ERROR)
