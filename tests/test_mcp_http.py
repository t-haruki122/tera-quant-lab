import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import timedelta
from ipaddress import ip_network
from types import SimpleNamespace

import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app import main
from app.auth import AUTH_REQUIRED
from app.mcp_http import AppCORSMiddleware
from app.services import api_token_service, user_service
from app.timeutil import to_db
from tests.test_api_tokens import bearer, create_token
from tests.test_tera_mcp import TOOL_NAMES

ORIGIN = "http://tql-lxc:8000"
RPC_HEADERS = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}


@asynccontextmanager
async def session(token, path="/mcp"):
    async with (
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app=main.app), headers=bearer(token)) as http,
        streamable_http_client(ORIGIN + path, http_client=http) as streams,
        ClientSession(*streams, read_timeout_seconds=5) as mcp,
    ):
        initialized = await mcp.initialize()
        assert initialized.server_info.name == "tera-quant-lab"
        yield mcp


def test_mcp_http_auth_and_origins(client, users, settings, monkeypatch, caplog):
    alice = users[0]
    valid = create_token(client, alice)["token"]
    expired = create_token(client, alice, expiry=30)["token"]
    then = to_db() + timedelta(days=31)
    monkeypatch.setattr(api_token_service, "to_db", lambda: then)
    for credentials in ({}, bearer("tql_" + "Z" * 43), bearer("tql_short"), bearer(expired), alice["headers"]):
        for method, path in (("POST", "/mcp"), ("GET", "/mcp/"), ("DELETE", "/mcp"), ("OPTIONS", "/mcp/")):
            response = client.request(method, ORIGIN + path, headers=credentials)
            assert response.status_code == 401 and response.json() == {"detail": AUTH_REQUIRED}
            assert response.headers["WWW-Authenticate"] == "Bearer"
            assert response.headers["Cache-Control"] == "no-store"
            assert "Content-Security-Policy" in response.headers

    headers = {**bearer(valid), **RPC_HEADERS}
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert client.post(ORIGIN + "/mcp", headers={**headers, "Origin": "https://foreign.invalid"}, json=body).status_code == 403
    with caplog.at_level(logging.DEBUG):
        allowed = client.post(ORIGIN + "/mcp/", headers={**headers, "Origin": ORIGIN, "X-Request-ID": valid}, json=body)
    assert allowed.status_code == 200
    assert valid not in caplog.text and allowed.headers["X-Request-ID"] != valid
    settings.mcp_allowed_hosts = ["tql-lxc:*"]
    assert client.post("http://elsewhere:8000/mcp", headers=headers, json=body).status_code == 421
    assert client.post(ORIGIN + "/mcp", headers=headers, json=body).status_code == 200
    assert client.post(ORIGIN + "/mcp", headers={**headers, "Content-Type": "application/json"},
                       content=b" " * (4 * 1024 * 1024 + 1)).status_code == 413

    async def proxy_origin_and_cors_preflight():
        settings.trusted_proxies = [ip_network("127.0.0.1")]
        cors = AppCORSMiddleware(main.app, allow_origins=["https://foreign.invalid"], allow_methods=["POST"])
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=cors), base_url=ORIGIN) as http:
            forwarded = await http.post("/mcp", headers={**headers, "X-Forwarded-Proto": "https",
                                                        "Origin": "https://tql-lxc:8000"}, json=body)
            assert forwarded.status_code == 200
            preflight = await http.options("/mcp", headers={"Origin": "https://foreign.invalid",
                                                            "Access-Control-Request-Method": "POST"})
            assert preflight.status_code == 403 and "Access-Control-Allow-Origin" not in preflight.headers

    client.portal.call(proxy_origin_and_cors_preflight)

    settings.rate_limit_enabled = True
    settings.rate_limit_data_requests = 1
    assert client.post(ORIGIN + "/mcp", headers=headers, json=body).status_code == 200
    limited = client.post(ORIGIN + "/mcp", headers=headers, json=body)
    assert limited.status_code == 429 and "Retry-After" in limited.headers


def test_mcp_sdk_calls_and_concurrent_user_isolation(client, users, monkeypatch):
    tokens = [create_token(client, user)["token"] for user in users]
    monkeypatch.setattr("yfinance.Ticker", lambda symbol: SimpleNamespace(info={
        "quoteType": "EQUITY", "currentPrice": 185, "shortName": symbol, "trailingPE": 22, "priceToBook": 3,
    }))

    async def scenario():
        async with session(tokens[0]) as mcp:
            tools = (await mcp.list_tools()).tools
            assert {tool.name for tool in tools} == TOOL_NAMES
            assert all(tool.annotations.read_only_hint for tool in tools)
            result = await mcp.call_tool("get_stock", {"symbol": "AAPL"})
            assert not result.is_error, result
            assert result.structured_content["price"]["price"] == 185
            assert result.structured_content["profile"]["name"] == "AAPL"
            assert result.structured_content["indicators"]["mix_index"] == 66

        arrived = asyncio.Event()
        ids = set()
        original = user_service.get_user_lists

        async def rendezvous(user_id, db):
            ids.add(user_id)
            if len(ids) == 2:
                arrived.set()
            await asyncio.wait_for(arrived.wait(), timeout=5)
            return await original(user_id, db)

        monkeypatch.setattr(user_service, "get_user_lists", rendezvous)

        async def get_own_watchlist(token, path):
            async with session(token, path) as mcp:
                result = await mcp.call_tool("get_watchlist", {})
                assert not result.is_error, result
                return result.structured_content["id"]

        results = await asyncio.gather(get_own_watchlist(tokens[0], "/mcp"), get_own_watchlist(tokens[1], "/mcp/"))
        assert results == [user["list_id"] for user in users]

    client.portal.call(scenario)


def test_mcp_http_disabled(settings, request):
    settings.mcp_http_enabled = False
    client = request.getfixturevalue("client")
    for path in ("/mcp", "/mcp/"):
        assert client.post(path).status_code == 404
        assert client.get(path).headers["Cache-Control"] == "no-store"
