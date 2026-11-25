"""カスタム例外・グローバルエラーハンドラー"""

import math

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

INTERNAL_ERROR = "サーバー内部でエラーが発生しました。しばらくしてから再試行してください。"


def internal_error_response() -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": INTERNAL_ERROR})


class SymbolNotFoundError(Exception):
    """シンボルが見つからない場合 (404)"""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.message = f"Symbol not found: {symbol}"
        super().__init__(self.message)


class RateLimitError(Exception):
    """APIレート制限 (429)"""

    def __init__(self, message: str = "API rate limit exceeded"):
        self.message = message
        super().__init__(self.message)


class ExternalAPIError(Exception):
    """外部APIエラー (502)"""

    def __init__(self, message: str = "External API error"):
        self.message = message
        super().__init__(self.message)


class UpstreamUnavailableError(ExternalAPIError):
    """Network failures and timeouts (503)."""


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
            message = error["msg"]
            if error["type"] == "value_error":
                message = message.removeprefix("Value error, ")
            elif field in {"start_date", "end_date"}:
                message = f"{field} は YYYY-MM-DD 形式の実在する日付を指定してください"
            elif field == "interval":
                message = "interval は 1d、1wk、1mo のいずれかを指定してください"
            elif field == "limit":
                bound = error.get("ctx", {}).get("ge") or error.get("ctx", {}).get("le")
                operator = "以上" if error["type"] == "greater_than_equal" else "以下"
                message = f"limit は {bound} {operator}の整数を指定してください" if bound else "limit は整数を指定してください"
            elif field in {"user_id", "list_id"}:
                message = f"{field} は 1 以上の有効な整数 ID を指定してください"
            elif field == "symbol":
                message = "symbol は銘柄を表す文字列を指定してください"
            errors.append({"type": error["type"], "loc": error["loc"], "msg": message, "input": error.get("input")})
        return JSONResponse(status_code=422, content={"detail": json_safe(jsonable_encoder(errors))})

    @app.exception_handler(HTTPException)
    async def handled_http_exception(request: Request, exc: HTTPException):
        if exc.status_code == 500:
            # Route this through the same logging and response path as other internal errors.
            raise exc
        return await http_exception_handler(request, exc)

    @app.exception_handler(SymbolNotFoundError)
    async def symbol_not_found_handler(request: Request, exc: SymbolNotFoundError):
        return JSONResponse(
            status_code=404,
            content={"detail": exc.message, "status_code": 404},
        )

    @app.exception_handler(RateLimitError)
    async def rate_limit_handler(request: Request, exc: RateLimitError):
        return JSONResponse(
            status_code=429,
            content={"detail": exc.message, "status_code": 429},
            headers={"Retry-After": "60"},
        )

    @app.exception_handler(UpstreamUnavailableError)
    async def unavailable_handler(request: Request, exc: UpstreamUnavailableError):
        return JSONResponse(status_code=503, content={"detail": exc.message, "status_code": 503})

    @app.exception_handler(ExternalAPIError)
    async def external_api_handler(request: Request, exc: ExternalAPIError):
        return JSONResponse(
            status_code=502,
            content={"detail": exc.message, "status_code": 502},
        )
