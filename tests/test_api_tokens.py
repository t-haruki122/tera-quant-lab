import asyncio
import hashlib
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx

from app import main
from app.auth import AUTH_REQUIRED
from app.services import api_token_service, finance_service, news_service, stock_service
from app.timeutil import to_db, utc_iso

RUN = {"symbol": "AAPL", "start_date": "2024-01-01", "end_date": "2024-01-12",
       "strategy": "momentum", "params": {"period": 1}}


def token_path(user):
    return f"/user/{user['id']}/api-tokens"


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def create_token(client, user, name="Claude Code", expiry=None):
    response = client.post(token_path(user), headers=user["headers"], json={"name": name, "expires_in_days": expiry})
    assert response.status_code == 201, response.text
    assert response.headers["Cache-Control"] == "no-store"
    return response.json()


def test_token_lifecycle_and_backup_exclusion(client, users, settings):
    alice, bob = users
    path = token_path(alice)
    for body in ({"name": "  "}, {"name": "名" * 51}, {"name": "valid", "expires_in_days": 31}):
        assert client.post(path, headers=alice["headers"], json=body).status_code == 422
    created = create_token(client, alice, "  Claude <Code>  ", 90)
    token = created.pop("token")
    assert api_token_service.TOKEN_PATTERN.fullmatch(token)
    assert created["name"] == "Claude <Code>" and created["token_prefix"] == token[:12]
    assert not created["expired"] and created["last_used_at"] is None
    assert datetime.fromisoformat(created["expires_at"]) - datetime.fromisoformat(created["created_at"]) == timedelta(days=90)
    with closing(sqlite3.connect(settings.database_url.split("///", 1)[1])) as db:
        row = db.execute("SELECT * FROM api_tokens").fetchone()
        assert hashlib.sha256(token.encode()).hexdigest() in row and all(token not in str(value) for value in row)
    listed = client.get(path, headers=alice["headers"])
    assert listed.json() == [created] and "token_hash" not in listed.text and token not in listed.text
    assert client.get(token_path(bob), headers=bob["headers"]).json() == []
    assert client.delete(f"{token_path(bob)}/{created['id']}", headers=bob["headers"]).status_code == 404
    assert client.get(f"/user/{bob['id']}/lists", headers=bearer(token)).status_code == 403
    assert client.get(f"/user/{alice['id']}/lists/{bob['list_id']}/summary", headers=bearer(token)).status_code == 404

    backup = client.get(f"/user/{alice['id']}/export", headers=alice["headers"])
    assert "api_tokens" not in backup.json() and created["token_prefix"] not in backup.text and token not in backup.text
    assert client.post(f"/user/{alice['id']}/import?mode=replace", headers=alice["headers"], content=backup.content).status_code == 200
    assert client.get(path, headers=alice["headers"]).json()[0]["id"] == created["id"]
    assert client.delete(f"{path}/{created['id']}", headers=alice["headers"]).status_code == 204
    assert client.get(path, headers=alice["headers"]).json() == []
    assert client.get("/user/me", headers=bearer(token)).status_code == 401


def test_expiry_throttle_limit_and_account_deletion(client, users, settings, monkeypatch):
    alice, bob = users
    path = token_path(alice)
    now = to_db()
    monkeypatch.setattr(api_token_service, "to_db", lambda: now)
    monkeypatch.setattr("app.routers.api_tokens.to_db", lambda: now)
    expired = create_token(client, alice, "期限あり", 30)
    now += timedelta(days=31)
    denied = client.get("/user/me", headers=bearer(expired["token"]))
    assert denied.status_code == 401 and denied.json() == {"detail": AUTH_REQUIRED}
    for index in range(9):
        active = create_token(client, alice, f"Token {index}")
    assert active["expires_at"] is None

    async def create_concurrently():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://testserver") as http:
            return await asyncio.gather(*(http.post(path, headers=alice["headers"], json={"name": str(index)})
                                          for index in range(2)))

    results = client.portal.call(create_concurrently)
    assert sorted(response.status_code for response in results) == [201, 409]
    assert "10 件" in next(response.json()["detail"] for response in results if response.status_code == 409)
    bob_token = create_token(client, bob)
    assert client.get(path, headers=alice["headers"]).json()[-1]["expired"] is True
    headers = bearer(active["token"])
    assert client.get("/user/me", headers=headers).json() == {"id": alice["id"]}

    def last_used():
        return next(item["last_used_at"] for item in client.get(path, headers=alice["headers"]).json()
                    if item["id"] == active["id"])

    first = last_used()
    assert first == utc_iso(now)
    now += timedelta(seconds=59)
    assert client.get("/user/me", headers=headers).status_code == 200 and last_used() == first
    now += timedelta(seconds=1)
    assert client.get("/user/me", headers=headers).status_code == 200 and last_used() == utc_iso(now)

    deleted = client.request("DELETE", f"/user/{alice['id']}", headers=alice["headers"], json={"current_password": "password123"})
    assert deleted.status_code == 204
    assert client.get("/user/me", headers=headers).status_code == 401
    with closing(sqlite3.connect(settings.database_url.split("///", 1)[1])) as db:
        assert db.execute("SELECT id FROM api_tokens").fetchall() == [(bob_token["id"],)]


def test_token_route_scope(client, users, monkeypatch):
    alice, bob = users
    token = create_token(client, alice)
    headers = bearer(token["token"])
    owner = f"/user/{alice['id']}"
    watchlist = f"{owner}/lists/{alice['list_id']}"
    monkeypatch.setattr("yfinance.Ticker", lambda symbol: SimpleNamespace(info={
        "currentPrice": 150, "regularMarketPrice": 150, "shortName": "Test stock", "currency": "USD",
        "quoteType": "EQUITY",
    }))
    monkeypatch.setattr(stock_service, "get_history", AsyncMock(return_value={"symbol": "AAPL", "history": [
        {"date": f"2024-01-{day:02d}", "open": 100 + day, "high": 102 + day, "low": 99 + day,
         "close": 101 + day, "volume": 1000} for day in range(1, 13)
    ]}))
    monkeypatch.setattr(finance_service, "get_financials", AsyncMock(return_value={"symbol": "AAPL"}))
    monkeypatch.setattr(finance_service, "get_financial_history", AsyncMock(return_value={"symbol": "AAPL", "history": []}))
    monkeypatch.setattr(news_service, "get_news", AsyncMock(return_value={"symbol": "AAPL", "news": []}))
    monkeypatch.setattr("app.routers.symbols.directory.search", AsyncMock(return_value=[]))
    for path in (f"{owner}/lists", f"{watchlist}/summary", f"{owner}/alerts", "/user/me", "/backtest/strategies",
                 "/backtest/runs", "/stock/AAPL", "/stock/AAPL/history", "/stock/AAPL/profile", "/stock/AAPL/indicators",
                 "/stock/AAPL/financials", "/stock/AAPL/financials/history", "/stock/AAPL/news", "/forex/USDJPY",
                 "/symbols/search?q=AAPL"):
        response = client.get(path, headers=headers)
        assert response.status_code == 200, (path, response.text)
    result = client.post("/backtest/run", headers=headers, json=RUN)
    assert result.status_code == 200 and result.json()["metrics"]["symbol"] == "AAPL"
    assert client.get("/backtest/runs", headers=headers).json() == {"runs": []}
    saved_run = {"kind": "single", "request": RUN, "result": result.json()}
    saved = client.post("/backtest/runs", headers=alice["headers"], json=saved_run)
    assert saved.status_code == 201, saved.text
    run_path = f"/backtest/runs/{saved.json()['id']}"
    assert client.get(run_path, headers=headers).status_code == 200
    assert client.get(run_path, headers=bearer(create_token(client, bob)["token"])).status_code == 404

    for method, path, body in (
        ("POST", f"{owner}/password", {"current_password": "password123", "new_password": "updated123"}),
        ("POST", f"{owner}/logout-all", None), ("DELETE", owner, {"current_password": "password123"}),
        ("PUT", watchlist, {"name": "changed"}), ("PUT", f"{watchlist}/cash", {"cash_jpy": 1}),
        ("POST", f"{watchlist}/items", {"symbol": "AAPL"}),
        ("PUT", f"{watchlist}/items/AAPL/shares", {"shares": 1}),
        ("POST", f"{owner}/alerts", {"symbol": "AAPL", "kind": "above", "threshold": 100}),
        ("PUT", f"{owner}/alerts/1", {"symbol": "AAPL", "kind": "above", "threshold": 100}),
        ("DELETE", f"{owner}/alerts/1", None), ("POST", f"{owner}/alerts/check", None),
        ("GET", f"{owner}/export", None), ("POST", f"{owner}/import", None),
        ("GET", f"{owner}/default-list", None), ("GET", watchlist, None), ("GET", f"{watchlist}/export.csv", None),
        ("GET", f"{owner}/memo/AAPL", None), ("PUT", f"{owner}/memo/AAPL", {"memo": "test"}),
        ("POST", "/backtest/runs", saved_run), ("PATCH", run_path, {"name": "changed"}), ("DELETE", run_path, None),
        ("POST", "/backtest/grid", {**RUN, "grid": {"period": [1, 2]}}),
        ("POST", "/backtest/portfolio", {**RUN, "symbols": ["AAPL"]}),
        ("POST", "/backtest/walkforward", {**RUN, "grid": {"period": [1, 2]}, "folds": 2}),
        ("GET", token_path(alice), None), ("POST", token_path(alice), {"name": "denied"}),
        ("DELETE", f"{token_path(alice)}/{token['id']}", None),
        ("GET", "/stock/AAPL/snapshot", None), ("GET", "/stock/AAPL/dividends/history", None),
    ):
        response = client.request(method, path, headers=headers, json=body)
        assert response.status_code == 401 and response.json() == {"detail": AUTH_REQUIRED}, (method, path, response.text)
