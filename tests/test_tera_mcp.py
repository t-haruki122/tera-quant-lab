"""Read-only MCP tools tested entirely against an in-memory HTTP transport."""

import asyncio
import json
import logging
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from mcp.client import Client
from mcp.server.mcpserver.exceptions import ToolError

from tera_mcp.client import Settings, TQLClient
from tera_mcp.server import create_server

PASSWORD = "mock-private-password"
TOKEN = "mock-private-token"
FRESH_TOKEN = "mock-refreshed-private-token"
ACCOUNT = Settings(username="alice", password=PASSWORD)
TOOL_NAMES = {
    "list_watchlists", "get_watchlist", "get_price_alerts", "get_stock", "get_financials",
    "get_price_history", "get_news", "get_exchange_rate", "list_backtest_strategies", "run_backtest",
    "list_saved_backtests", "get_saved_backtest", "search_symbols",
}


def mock_client(routes, settings=ACCOUNT):
    requests = []

    async def handle(request):
        endpoint = (request.method, request.url.raw_path.decode().split("?", 1)[0])
        assert endpoint in routes, f"Unexpected MCP endpoint {endpoint}"
        requests.append(endpoint)
        response = routes[endpoint]
        if asyncio.iscoroutinefunction(response):
            return await response(request)
        if callable(response):
            return response(request)
        return httpx.Response(200, json=response)

    return TQLClient(settings, transport=httpx.MockTransport(handle)), requests


async def call(server, name, **arguments):
    result = await server.call_tool(name, arguments)
    assert result.structured_content is not None
    return result.structured_content


def test_symbol_search_is_public_and_allowlisted():
    def result(request):
        assert "Authorization" not in request.headers
        assert dict(request.url.params) == {"q": "トヨタ", "limit": "5"}
        return httpx.Response(200, json={"items": [{"symbol": "7203.T", "source": "jpx"}]})

    async def scenario():
        api, requests = mock_client({("GET", "/symbols/search"): result}, Settings())
        async with api:
            server = create_server(api)
            assert (await call(server, "search_symbols", query=" トヨタ ", limit=5))["items"][0]["symbol"] == "7203.T"
            with pytest.raises(ToolError):
                await api.get("/symbols/refresh")
            assert requests == [("GET", "/symbols/search")]

    asyncio.run(scenario())


def test_lazy_login_reuses_token_and_default_watchlist():
    watchlist = {
        "id": 7, "name": "既定", "items": [{
            "symbol": "9433.T", "name": "KDDI", "price": 25, "currency": "JPY", "market_cap": 400,
            "mix_index": 12, "per": 10, "pbr": 1.2, "dividend_yield": 3, "tags": ["配当"], "memo": "確認",
            "stale": True, "cached_at": "2025-01-01T00:00:00Z", "error": None,
            "shares": 2.5, "value_jpy": 62.5, "weight_pct": 50,
        }], "cash_jpy": 62, "portfolio": {"cash_ratio_pct": 50, "total_value_jpy": 125},
    }
    lists = {"lists": [{"id": 7, "name": "既定"}], "default_list_id": 7}
    alerts = {"alerts": [{"symbol": "HPQ", "kind": "above", "threshold": 50}]}

    def login(request):
        assert json.loads(request.content) == {"username": "alice", "password": PASSWORD}
        assert "Authorization" not in request.headers
        return httpx.Response(200, json={"id": 42, "access_token": TOKEN})

    def authenticated(payload):
        def response(request):
            assert request.headers["Authorization"] == f"Bearer {TOKEN}"
            return httpx.Response(200, json=payload)

        return response

    def strategies(request):
        assert "Authorization" not in request.headers
        return httpx.Response(200, json={"strategies": []})

    routes = {
        ("GET", "/backtest/strategies"): strategies,
        ("POST", "/user/login"): login,
        ("GET", "/user/42/lists"): authenticated(lists),
        ("GET", "/user/42/lists/7/summary"): authenticated(watchlist),
        ("GET", "/user/42/alerts"): authenticated(alerts),
    }

    async def scenario():
        api, requests = mock_client(routes)
        async with api:
            server = create_server(api)
            assert await call(server, "list_backtest_strategies") == {"strategies": []}
            assert requests == [("GET", "/backtest/strategies")]
            assert await call(server, "list_watchlists") == lists
            assert await call(server, "get_watchlist") == watchlist
            assert await call(server, "get_watchlist", list_id=7) == watchlist
            assert await call(server, "get_price_alerts") == alerts
            assert await call(server, "list_backtest_strategies") == {"strategies": []}
            assert requests.count(("POST", "/user/login")) == 1
            assert requests.count(("GET", "/user/42/lists")) == 2
            assert requests.count(("GET", "/user/42/lists/7/summary")) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("retry_status", [200, 401])
def test_relogin_once_and_redact_http_errors(retry_status, caplog):
    tokens = iter([TOKEN, FRESH_TOKEN])
    attempts = []

    def login(request):
        return httpx.Response(200, json={"id": 42, "access_token": next(tokens)})

    def watchlist(request):
        attempts.append(request.headers["Authorization"])
        if len(attempts) == 1:
            return httpx.Response(401, json={"detail": "期限切れ"})
        if retry_status == 401:
            return httpx.Response(401, json={"detail": f"認証に失敗しました {TOKEN} {FRESH_TOKEN} {PASSWORD}"})
        return httpx.Response(200, json={"items": [], "portfolio": None})

    async def scenario():
        api, requests = mock_client({("POST", "/user/login"): login, ("GET", "/user/42/lists/7/summary"): watchlist})
        async with api, Client(create_server(api), mode="legacy") as mcp:
            reply = await mcp.call_tool("get_watchlist", {"list_id": 7})
            if retry_status == 401:
                assert reply.is_error
                assert "HTTP 401 認証に失敗しました" in reply.model_dump_json()
            else:
                assert not reply.is_error
                assert reply.structured_content == {"items": [], "portfolio": None}
        assert requests == [("POST", "/user/login"), ("GET", "/user/42/lists/7/summary")] * 2
        assert attempts == [f"Bearer {TOKEN}", f"Bearer {FRESH_TOKEN}"]
        for secret in (PASSWORD, TOKEN, FRESH_TOKEN):
            assert secret not in reply.model_dump_json()
            assert secret not in caplog.text

    with caplog.at_level(logging.INFO):
        asyncio.run(scenario())


def test_missing_credentials_and_no_default_list():
    async def scenario():
        api, requests = mock_client({("GET", "/backtest/strategies"): {"strategies": []}}, Settings())
        async with api:
            server = create_server(api)
            assert await call(server, "list_backtest_strategies") == {"strategies": []}
            with pytest.raises(ToolError, match="TQL_USERNAME と TQL_PASSWORD を設定してください"):
                await call(server, "get_watchlist", list_id=7)
        assert requests == [("GET", "/backtest/strategies")]

        api, requests = mock_client({
            ("POST", "/user/login"): {"id": 42, "access_token": TOKEN},
            ("GET", "/user/42/lists"): {"lists": [], "default_list_id": None},
        })
        async with api:
            with pytest.raises(ToolError, match="既定のウォッチリストがありません"):
                await call(create_server(api), "get_watchlist")
        assert requests == [("POST", "/user/login"), ("GET", "/user/42/lists")]

    asyncio.run(scenario())


def test_stock_partial_failure_and_concurrency():
    async def scenario():
        started = asyncio.Event()
        paths = set()
        price = {"symbol": "^N225", "price": 42, "currency": "JPY", "stale": True, "cached_at": "2025-01-01T00:00:00Z"}

        async def fetch(request):
            paths.add(request.url.raw_path)
            if len(paths) == 3:
                started.set()
            await started.wait()
            if request.url.path.endswith("/profile"):
                return httpx.Response(404, json={"detail": "プロフィールがありません"})
            return httpx.Response(200, json={"per": 21} if request.url.path.endswith("/indicators") else price)

        api, requests = mock_client({("GET", f"/stock/%5EN225{suffix}"): fetch for suffix in ("", "/profile", "/indicators")})
        async with api:
            server = create_server(api)
            result = await asyncio.wait_for(call(server, "get_stock", symbol=" ^n225 "), timeout=3)
            assert result == {"symbol": "^N225", "price": price, "profile": {"error": "HTTP 404 プロフィールがありません"},
                              "indicators": {"per": 21}}
            with pytest.raises(ToolError, match="symbol は英数字"):
                await call(server, "get_stock", symbol="../user/login")
        assert len(requests) == 3

    asyncio.run(scenario())


def test_history_summary_sampling_and_default_dates(monkeypatch):
    monkeypatch.setattr("tera_mcp.server.utcnow", lambda: datetime(2024, 2, 29, tzinfo=UTC))
    rows = [
        {"date": (date(2023, 4, 1) + timedelta(days=i)).isoformat(), "close": 100 + i,
         "high": 1000 if i == 51 else 101 + i, "low": 2 if i == 52 else 99 + i, "volume": 100}
        for i in range(300)
    ]

    def history(request):
        if request.url.params["interval"] == "1d":
            assert dict(request.url.params) == {"interval": "1d", "start_date": "2023-03-01", "end_date": "2024-02-29"}
            return httpx.Response(200, json={"symbol": "9433.T", "history": rows, "stale": True, "cached_at": "2024-02-28T10:00:00Z"})
        assert dict(request.url.params) == {"interval": "1wk", "start_date": "2023-04-01"}
        return httpx.Response(200, json={"symbol": "9433.T", "history": []})

    async def scenario():
        api, _ = mock_client({("GET", "/stock/9433.T/history"): history}, Settings())
        async with api:
            server = create_server(api)
            payload = await call(server, "get_price_history", symbol="9433.t")
            data = payload["history"]
            assert len(data) == 120
            assert data[0] == {"date": rows[0]["date"], "close": 100}
            assert data[-1] == {"date": rows[-1]["date"], "close": 399}
            assert payload["summary"] == {
                "bar_count": 300, "first_date": rows[0]["date"], "last_date": rows[-1]["date"],
                "first_close": 100, "last_close": 399, "change_pct": pytest.approx(299),
                "high": 1000, "high_date": rows[51]["date"], "low": 2, "low_date": rows[52]["date"],
            }
            assert payload["stale"] is True and payload["cached_at"] == "2024-02-28T10:00:00Z"
            empty = await call(server, "get_price_history", symbol="9433.T", interval="1wk", start_date="2023-04-01")
            assert empty["history"] == [] and empty["summary"]["bar_count"] == 0
            assert empty["summary"]["high"] is None and empty["summary"]["change_pct"] is None

    asyncio.run(scenario())


def test_public_financials_news_and_exchange_rate():
    news = {"title": "決算発表", "publisher": "発行元", "published_at": "2025-02-01T00:00:00Z", "url": "https://example.com/1"}
    financials = {"symbol": "HPQ", "revenue": 123, "stale": True, "cached_at": "2025-02-01T00:00:00Z"}
    annual = {"symbol": "HPQ", "history": [{"period": "2024", "revenue": 100}]}

    async def scenario():
        api, requests = mock_client({
            ("GET", "/stock/HPQ/financials"): financials,
            ("GET", "/stock/HPQ/financials/history"): annual,
            ("GET", "/stock/USDJPY%3DX/financials"): {"symbol": "USDJPY=X"},
            ("GET", "/stock/HPQ/news"): {"news": [{**news, "source": "provider"}] * 35, "stale": False},
            ("GET", "/forex/USDJPY"): {"pair": "USDJPY=X", "rate": 150},
            ("GET", "/forex/USDJPY%3DX"): {"pair": "USDJPY=X", "rate": 150},
        }, Settings())
        async with api:
            server = create_server(api)
            assert await call(server, "get_financials", symbol="hpq", include_history=False) == {"financials": financials}
            assert requests == [("GET", "/stock/HPQ/financials")]
            assert await call(server, "get_financials", symbol="HPQ") == {"financials": financials, "history": annual}
            assert await call(server, "get_financials", symbol="USDJPY=X", include_history=False) == {"financials": {"symbol": "USDJPY=X"}}
            for limit, expected in ((-1, 1), (10, 10), (99, 30)):
                assert await call(server, "get_news", symbol="HPQ", limit=limit) == {"news": [news] * expected, "stale": False}
            assert (await call(server, "get_exchange_rate"))["rate"] == 150
            assert (await call(server, "get_exchange_rate", pair="usdjpy=x"))["pair"] == "USDJPY=X"
            with pytest.raises(ToolError, match="為替ペア"):
                await call(server, "get_exchange_rate", pair="USD/JPY")

    asyncio.run(scenario())


def test_run_and_saved_backtest_compaction():
    curve = [{"date": i, "equity": 10 + i, "benchmark_equity": 10} for i in range(203)]
    trades = [{"date": i, "side": "buy"} for i in range(80)]
    stats = {"metrics": {"return_pct": 4}, "benchmark_metrics": {"return_pct": 2}, "warnings": ["テスト警告"]}
    result = {**stats, "equity_curve": curve, "trades": trades}
    saved = {"id": 3, "kind": "single", "request": {"symbol": "HPQ"}}
    args = {
        "symbol": "HPQ", "strategy": "sma_cross", "params": {"fast": 5, "slow": 20},
        "start_date": "2024-01-01", "end_date": "2025-01-01", "interval": "1wk", "execution": "close",
        "fee_rate": 0.01, "slippage_bps": 20, "initial_capital": 30_000, "eps_lag_days": 45, "risk_free": 0.02,
    }

    def run(request):
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert json.loads(request.content) == args
        assert request.extensions["timeout"]["read"] == 120
        return httpx.Response(200, json=result)

    async def scenario():
        api, requests = mock_client({
            ("POST", "/user/login"): {"id": 42, "access_token": TOKEN},
            ("POST", "/backtest/run"): run,
            ("GET", "/backtest/runs"): {"runs": [saved]},
            ("GET", "/backtest/runs/3"): {**saved, "result": result},
            ("GET", "/backtest/runs/4"): {"id": 4, "kind": "grid", "result": {"equity_curve": curve, "results": [stats]}},
        })
        async with api:
            server = create_server(api)
            run_result = await call(server, "run_backtest", **args)
            assert run_result["trade_count"] == 80 and run_result["trades"] == trades[-20:]
            assert len(run_result["equity_curve"]) == 100
            assert run_result["equity_curve"][0] == curve[0] and run_result["equity_curve"][-1] == curve[-1]
            assert {key: run_result[key] for key in stats} == stats
            assert await call(server, "list_saved_backtests") == {"runs": [saved]}
            assert await call(server, "get_saved_backtest", run_id=3) == {**saved, "result": run_result}
            grid = await call(server, "get_saved_backtest", run_id=4)
            assert grid["result"] == {"equity_curve": run_result["equity_curve"], "results": [stats]}
        assert requests.count(("POST", "/user/login")) == 1

    asyncio.run(scenario())


def test_http_error_mapping_and_redirects(caplog):
    def disconnected(request):
        raise httpx.ConnectError(f"内部通信例外 {PASSWORD}", request=request)

    async def scenario():
        api, _ = mock_client({
            ("POST", "/user/login"): lambda _: httpx.Response(422, json={"detail": [
                {"msg": "パスワードを確認してください", "input": PASSWORD},
            ]}),
            ("GET", "/stock/HPQ/financials"): lambda _: httpx.Response(429, json={"detail": "要求が多すぎます"}, headers={"Retry-After": "45"}),
            ("GET", "/forex/USDJPY"): disconnected,
            ("GET", "/backtest/strategies"): lambda _: httpx.Response(307, headers={"Location": "/user/42/default-list"}),
        })
        async with api:
            server = create_server(api)
            with pytest.raises(ToolError, match="HTTP 422 パスワードを確認してください") as exc:
                await call(server, "get_watchlist", list_id=7)
            assert PASSWORD not in str(exc.value)
            with pytest.raises(ToolError, match="HTTP 429 要求が多すぎます.*Retry-After: 45"):
                await call(server, "get_financials", symbol="HPQ")
            with pytest.raises(ToolError) as exc:
                await call(server, "get_exchange_rate")
            assert "アプリに接続できません（TQL_BASE_URL=http://127.0.0.1:8000）。サーバーが起動しているか確認してください。" in str(exc.value)
            with pytest.raises(ToolError, match="HTTP 307"):
                await call(server, "list_backtest_strategies")
            with pytest.raises(ToolError, match="許可されていない API"):
                await api.get("/user/42/default-list")

    with caplog.at_level(logging.INFO):
        asyncio.run(scenario())
    assert PASSWORD not in caplog.text


def test_mcp_protocol_tool_metadata_and_call():
    async def scenario():
        payload = {"pair": "USDJPY=X", "rate": 145, "timestamp": "2025-01-01T00:00:00Z", "stale": False}
        api, requests = mock_client({("GET", "/forex/USDJPY"): payload}, Settings())
        async with api, Client(create_server(api), mode="legacy") as mcp:
            tools = await mcp.list_tools()
            assert {tool.name for tool in tools.tools} == TOOL_NAMES
            assert len(tools.tools) == len(TOOL_NAMES)
            for tool in tools.tools:
                assert tool.annotations is not None
                assert tool.annotations.read_only_hint is True
                assert tool.annotations.idempotent_hint is True
                assert tool.annotations.open_world_hint is (tool.name not in {
                    "list_watchlists", "get_price_alerts", "list_backtest_strategies", "list_saved_backtests", "get_saved_backtest",
                })
            assert "読み取り専用" in (mcp.session.instructions or "")
            result = await mcp.call_tool("get_exchange_rate", {})
            assert not result.is_error and result.structured_content == payload
            assert requests == [("GET", "/forex/USDJPY")]

    asyncio.run(scenario())


def test_entrypoint_boots_without_importing_app_or_database():
    subprocess.run([sys.executable, "-c", """
import os
import runpy
import sys
from unittest.mock import patch

for key in ('TQL_BASE_URL', 'TQL_USERNAME', 'TQL_PASSWORD', 'TQL_TIMEOUT_SECONDS'):
    os.environ.pop(key, None)
with patch('mcp.server.mcpserver.MCPServer.run_stdio_async') as run:
    runpy.run_module('tera_mcp', run_name='__main__')
    run.assert_awaited_once()
assert not {'app.main', 'app.config', 'app.database', 'sqlalchemy.engine'}.intersection(sys.modules)
"""], check=True, capture_output=True, timeout=15)


def test_settings_only_use_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("TQL_BASE_URL=http://dotenv.example\nTQL_PASSWORD=dotenv-password\n", encoding="utf-8")
    for key in ("TQL_BASE_URL", "TQL_USERNAME", "TQL_PASSWORD", "TQL_TIMEOUT_SECONDS"):
        monkeypatch.delenv(key, raising=False)
    assert Settings.from_env() == Settings()
    monkeypatch.setenv("TQL_BASE_URL", "https://localhost:8080/subpath///")
    monkeypatch.setenv("TQL_TIMEOUT_SECONDS", "42.5")
    assert Settings.from_env() == Settings(base_url="https://localhost:8080/subpath", timeout_seconds=42.5)
    monkeypatch.setenv("TQL_TIMEOUT_SECONDS", "nan")
    with pytest.raises(ValueError, match="TQL_TIMEOUT_SECONDS"):
        Settings.from_env()
    for url in ("file:///stock_data.db", "http:///no-host", "https://user:secret@localhost"):
        with pytest.raises(ValueError, match="TQL_BASE_URL"):
            Settings(base_url=url)
