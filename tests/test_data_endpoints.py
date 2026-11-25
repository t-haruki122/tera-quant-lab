from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app import main
from app.auth import create_access_token
from app.config import Settings
from app.routers import backtest, stock
from app.services import forex_service, stock_service

BACKTEST_BODY = {"symbol": "7203.T", "start_date": "2024-01-01", "end_date": "2024-02-01"}


@pytest.mark.parametrize(("path", "message"), [
    ("/stock/X/history?start_date=2024-13-01", "実在する日付"),
    ("/stock/X/history?end_date=2024-02-30", "実在する日付"),
    ("/stock/X/history?end_date=2024-1-02", "YYYY-MM-DD"),
    ("/stock/X/history?start_date=1704067200", "YYYY-MM-DD"),
    ("/stock/X/history?start_date=2024-02-02&end_date=2024-02-01", "以前にしてください"),
    ("/stock/X/history?interval=2wk", "いずれかを指定してください"),
    ("/stock/X/financials/history?limit=11", "10 以下"),
    ("/stock/X/dividends/history?limit=0", "1 以上"),
    ("/stock/X/dividends/history?limit=oops", "整数を指定してください"),
    ("/stock/%E6%A0%AA/news", "20文字以内"),
    ("/stock/abcdefghijklmnopqrstu", "20文字以内"),
    ("/forex/USD1PY=X", "為替ペア"),
    ("/forex/USDJPY=Y", "為替ペア"),
])
def test_bad_data_parameters(client, path, message):
    response = client.get(path)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert message in (detail if isinstance(detail, str) else detail[0]["msg"])
    if isinstance(detail, list):
        assert all(set(error) == {"type", "loc", "msg", "input"} for error in detail)


def test_validation_error_keeps_input_and_hides_date_parser_context(client):
    response = client.get("/stock/X/history?start_date=2024-13-01")
    assert response.status_code == 422
    assert response.json() == {"detail": [{
        "type": "date_from_datetime_parsing",
        "loc": ["query", "start_date"],
        "msg": "start_date は YYYY-MM-DD 形式の実在する日付を指定してください",
        "input": "2024-13-01",
    }]}


def test_default_data_limit(monkeypatch):
    monkeypatch.delenv("RATE_LIMIT_DATA_REQUESTS", raising=False)
    monkeypatch.delenv("RATE_LIMIT_DATA_WINDOW_SECONDS", raising=False)
    defaults = Settings(_env_file=None)
    assert (defaults.rate_limit_data_requests, defaults.rate_limit_data_window_seconds) == (600, 60)


def test_history_calendar_span_and_normalization(client, settings, monkeypatch):
    settings.history_max_span_years = 1
    monkeypatch.setattr(stock, "utcnow", lambda: datetime(2025, 3, 1, tzinfo=UTC))
    history = AsyncMock(return_value={"symbol": "^N225", "history": []})
    monkeypatch.setattr(stock_service, "get_history", history)
    path = "/stock/%20%5En225%20/history?start_date=2024-02-29&interval=1WK"
    assert client.get(path).json()["detail"] == "株価履歴の取得期間は最大 1 年です"
    under = client.get(path + "&end_date=2025-02-28")
    assert under.status_code == 200
    symbol, _, start, end, interval = history.call_args.args
    assert (symbol, start, end, interval) == ("^N225", "2024-02-29", "2025-02-28", "1wk")
    history.assert_awaited_once()


def test_user_symbols_and_path_ids(client, users):
    user = users[0]
    headers = user["headers"]
    list_path = f"/user/{user['id']}/lists/{user['list_id']}"
    response = client.post(f"{list_path}/items", headers=headers, json={"symbol": " 7203.t "})
    assert response.json()["symbol"] == "7203.T"
    response = client.post(f"{list_path}/items", headers=headers, json={"symbol": "bad/path"})
    assert response.status_code == 422 and "20文字以内" in response.json()["detail"][0]["msg"]
    response = client.get(f"/user/{user['id']}/memo/a%3Fb", headers=headers)
    assert response.status_code == 422 and "20文字以内" in response.json()["detail"][0]["msg"]
    for path in (f"/user/{user['id']}/lists/{2**63}", "/user/0/lists", "/user/nope/lists"):
        response = client.get(path, headers=headers)
        assert response.status_code == 422 and "整数 ID" in response.json()["detail"][0]["msg"]


@pytest.fixture
def data_services(monkeypatch):
    forex = AsyncMock(return_value={"pair": "USDJPY=X", "rate": 150, "timestamp": "2026-11-26"})
    history = AsyncMock(return_value={"symbol": "X", "history": []})
    job = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(forex_service, "get_exchange_rate", forex)
    monkeypatch.setattr(stock_service, "get_history", history)
    monkeypatch.setattr(backtest, "_run_job", job)
    return forex, history, job


def test_data_limit_ip_retry_after_and_health_exemption(client, settings, data_services):
    settings.rate_limit_enabled = True
    settings.rate_limit_data_requests = 1
    settings.rate_limit_data_window_seconds = 30
    assert client.get("/forex/%20usdjpy=x%20").status_code == 200
    data_services[0].assert_awaited_once_with("USDJPY=X")
    limited = client.get("/stock/X/history", headers={"X-Forwarded-For": "203.0.113.10"})
    assert limited.status_code == 429
    assert 1 <= int(limited.headers["Retry-After"]) <= 30
    assert limited.json() == {"detail": "試行回数が多すぎます。しばらくしてから再試行してください。"}
    data_services[1].assert_not_called()
    assert all(client.get("/health").status_code == 200 for _ in range(3))
    assert client.get("/backtest/strategies").status_code == 200
    other = TestClient(main.app, client=("203.0.113.20", 30000))
    try:
        assert other.get("/stock/X/history").status_code == 200
    finally:
        other.close()


def test_data_limit_buckets_for_verified_users_and_anonymous_clients(client, users, settings, data_services):
    settings.rate_limit_enabled = True
    settings.rate_limit_data_requests = 1
    alice, bob = (user["headers"] for user in users)
    assert client.get("/forex/USDJPY", headers=alice).status_code == 200
    assert client.post("/backtest/run", headers=alice, json=BACKTEST_BODY).status_code == 429
    grid = {**BACKTEST_BODY, "grid": {"short_window": [10, 20]}}
    assert client.post("/backtest/grid", headers=bob, json=grid).status_code == 200
    assert client.get("/forex/USDJPY", headers=bob).status_code == 429
    assert client.get("/forex/USDJPY", headers={"Authorization": "Bearer invalid"}).status_code == 200
    assert client.get("/stock/X/history", headers={"Authorization": f"Bearer {create_access_token(99)}"}).status_code == 429
    assert client.post("/backtest/run", json=BACKTEST_BODY).status_code == 429
    data_services[2].assert_awaited_once()


def test_disabled_data_limit(client, settings, data_services):
    assert not settings.rate_limit_enabled
    settings.rate_limit_data_requests = 1
    assert [client.get("/stock/X/history").status_code for _ in range(3)] == [200, 200, 200]
    assert data_services[1].await_count == 3
