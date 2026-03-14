"""カスタム例外・グローバルエラーハンドラー"""

from fastapi import FastAPI, Request
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
    """外部APIエラー (500)"""

    def __init__(self, message: str = "External API error"):
        self.message = message
        super().__init__(self.message)


def register_exception_handlers(app: FastAPI):
    """FastAPIに例外ハンドラーを登録"""

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
        )

    @app.exception_handler(ExternalAPIError)
    async def external_api_handler(request: Request, exc: ExternalAPIError):
        return JSONResponse(
            status_code=500,
            content={"detail": exc.message, "status_code": 500},
        )
