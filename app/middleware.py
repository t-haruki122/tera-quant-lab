"""Security headers, request correlation, and unhandled error responses."""

import logging
import re
import time
from collections.abc import Callable
from contextvars import ContextVar
from uuid import uuid4

from starlette._utils import get_route_path
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import get_settings
from app.exceptions import internal_error_response
from app.rate_limit import client_ip, forwarded_scheme

DEFAULT_CSP = "; ".join((
    "default-src 'self'",
    "script-src 'self' https://cdn.jsdelivr.net",
    "style-src 'self' https://fonts.googleapis.com",
    "font-src https://fonts.gstatic.com",
    "connect-src 'self'",
    "img-src 'self' data:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
))

# Swagger's inline script contains the runtime OpenAPI URL, including root_path.
# ReDoc emits styles and uses a blob worker for its search index.
DOCS_CSP = "; ".join((
    "default-src 'self'",
    "script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'",
    "style-src 'self' https://cdn.jsdelivr.net https://fonts.googleapis.com 'unsafe-inline'",
    "font-src https://fonts.gstatic.com",
    "connect-src 'self'",
    "img-src 'self' data: https://fastapi.tiangolo.com",
    "worker-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
))

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}

request_id = ContextVar("request_id", default="-")


class RequestIDFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get()
        return True


logger = logging.getLogger(__name__)
logger.addFilter(RequestIDFilter())


class RequestMiddleware:
    def __init__(self, app: ASGIApp, record_request: Callable[[Scope, float, int], None], docs_paths: frozenset[str]):
        self.app = app
        self.record_request = record_request
        self.docs_paths = docs_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        path = get_route_path(scope)
        incoming = request.headers.getlist("x-request-id")
        correlation_id = incoming[0] if len(incoming) == 1 and re.fullmatch(r"[A-Za-z0-9._-]{1,64}", incoming[0]) else uuid4().hex
        request.state.request_id = correlation_id
        token = request_id.set(correlation_id)
        settings = get_settings()
        ip = client_ip(request, settings)
        if scheme := forwarded_scheme(request, settings):
            scope["scheme"] = scheme
        start = time.perf_counter()
        status = 500

        async def send_with_headers(message: Message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers[name] = value
                if path == "/" or path.startswith("/static/"):
                    headers["Cache-Control"] = "no-cache"
                docs_html = (
                    status == 200
                    and path in self.docs_paths
                    and headers.get("content-type", "").startswith("text/html")
                )
                headers["Content-Security-Policy"] = (
                    settings.content_security_policy or (DOCS_CSP if docs_html else DEFAULT_CSP)
                )
                headers["X-Request-ID"] = correlation_id
                if settings.hsts_enabled:
                    headers["Strict-Transport-Security"] = "max-age=31536000"
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        finally:
            try:
                duration = time.perf_counter() - start
                self.record_request(scope, duration, status)
                logger.info("HTTP %s %s -> %s (%.2f ms) client=%s", request.method, request.url.path,
                            status, duration * 1000, ip)
            finally:
                request_id.reset(token)


class InternalErrorMiddleware:
    """Render internal errors within the CORS middleware and request context."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = False

        async def track_start(message: Message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, track_start)
        except Exception:
            logger.exception("Unhandled exception during %s %s", scope["method"], scope["path"])
            if started:
                raise
            await internal_error_response()(scope, receive, send)
