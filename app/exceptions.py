"""カスタム例外・グローバルエラーハンドラー"""

import math

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


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
        return JSONResponse(status_code=422, content={"detail": json_safe(jsonable_encoder(exc.errors()))})

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
