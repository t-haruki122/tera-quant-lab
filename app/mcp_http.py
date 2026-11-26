"""Authenticate and serve MCP through the application's HTTP stack."""

from contextlib import asynccontextmanager
from contextvars import ContextVar

import httpx
from fastapi import FastAPI, HTTPException
from mcp.server.transport_security import TransportSecuritySettings
from starlette._utils import get_route_path
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.types import Receive, Scope, Send

from app import database
from app.auth import bearer, require_authenticated_user
from app.config import Settings, get_settings
from app.rate_limit import limiter
from app.services.api_token_service import find_token_user
from tera_mcp.client import RequestIdentity, TQLClient
from tera_mcp.client import Settings as ClientSettings
from tera_mcp.server import create_server

MCP_PATHS = ("/mcp", "/mcp/")
identity = ContextVar[RequestIdentity]("mcp_request_identity")


class AppCORSMiddleware(CORSMiddleware):
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # MCP preflights must also pass through the endpoint's Origin and Bearer checks.
        if scope["type"] == "http" and get_route_path(scope) in MCP_PATHS:
            await self.app(scope, receive, send)
        else:
            await super().__call__(scope, receive, send)


@asynccontextmanager
async def serve_mcp(app: FastAPI, settings: Settings):
    if not settings.mcp_http_enabled:
        yield
        return
    async with TQLClient(
        ClientSettings(), transport=httpx.ASGITransport(app=app), request_identity=identity.get,
    ) as client:
        server = create_server(client)
        # The gateway checks the request's own origin and optional Host allowlist.
        # Explicit settings disable the SDK's default localhost-only checks, but keep its body size limit.
        sdk_app = server.streamable_http_app(
            stateless_http=True, json_response=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
        async with server.session_manager.run():
            app.state.mcp_http = sdk_app
            try:
                yield
            finally:
                del app.state.mcp_http


class MCPGateway:
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1000})
            return
        request = Request(scope)
        sdk_app = getattr(request.app.state, "mcp_http", None)
        if sdk_app is None:
            raise HTTPException(404)

        settings = get_settings()
        hosts = request.headers.getlist("host")
        host = hosts[0].lower() if len(hosts) == 1 else ""
        if settings.mcp_allowed_hosts and not any(
            host == allowed.lower() or (allowed.endswith(":*") and host.rpartition(":")[0] == allowed[:-2].lower()
                                       and host.rpartition(":")[2].isascii() and host.rpartition(":")[2].isdecimal())
            for allowed in settings.mcp_allowed_hosts
        ):
            raise HTTPException(421, "許可されていないホストです")

        origins = request.headers.getlist("origin")
        default_port = ":443" if request.url.scheme == "https" else ":80"
        own_origin = f"{request.url.scheme}://{host.removesuffix(default_port)}"
        if origins and (not host or origins != [own_origin]):
            raise HTTPException(403, "許可されていないオリジンです")

        credentials = await bearer(request) if len(request.headers.getlist("authorization")) == 1 else None
        if credentials is None:
            require_authenticated_user(None)
            return
        async with database.AsyncSessionLocal() as db:
            user = require_authenticated_user(await find_token_user(credentials.credentials, db))
        limiter.data(request, settings, user_id=user.id)

        token = identity.set(RequestIdentity(user.id, credentials.credentials))
        try:
            # Canonicalize the trailing slash without a redirect. The SDK doesn't need the raw credential.
            sdk_scope = {
                **scope,
                "path": scope["path"].removesuffix("/"),
                "headers": [(key, value) for key, value in scope["headers"] if key != b"authorization"],
            }
            await sdk_app(sdk_scope, receive, send)
        finally:
            identity.reset(token)
