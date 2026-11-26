import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Event
from unittest.mock import AsyncMock

import pytest

from app.exceptions import SymbolNotFoundError, UpstreamUnavailableError
from app.routers import backtest
from app.services import profile_service, stock_service
from app.timeutil import to_db

BASE = {
    "start_date": "2024-01-02", "end_date": "2024-01-13", "strategy": "momentum",
    "params": {"period": 1}, "execution": "close",
}
REQUESTS = {
    "single": ("run", {**BASE, "symbol": " aaa "}),
    "grid": ("grid", {**BASE, "symbol": "AAA", "grid": {"period": [1, 2]}}),
    "portfolio": ("portfolio", {**BASE, "symbols": ["AAA", "^n225"]}),
    "walkforward": ("walkforward", {**BASE, "symbol": "AAA", "grid": {"period": [1, 2]}, "folds": 2}),
}


@pytest.fixture
def history(monkeypatch):
    async def load(symbol, db, start, end, interval, *, warmup_days):
        assert warmup_days >= 11
        return {"history": [
            {"date": f"2024-01-{index:02}", "open": value, "close": value}
            for index, value in enumerate([10, 12, 15, 14, 20, 22, 21, 25, 29, 26, 30, 32, 33], 1)
        ]}

    monkeypatch.setattr(stock_service, "get_history", load)
    monkeypatch.setattr(stock_service, "get_current_price", AsyncMock(return_value={"currency": "USD"}))
    return load


def run_result(client, headers, kind="single"):
    endpoint, request = REQUESTS[kind]
    response = client.post(f"/backtest/{endpoint}", headers=headers, json=request)
    assert response.status_code == 200, response.text
    return {"kind": kind, "request": request, "result": response.json()}


def test_advanced_endpoints_auth_and_validation(client, users, history):
    headers = users[0]["headers"]
    for kind in ("portfolio", "walkforward"):
        endpoint, body = REQUESTS[kind]
        assert client.post(f"/backtest/{endpoint}", json=body).status_code == 401
    for endpoint, body, message in [
        ("portfolio", {**REQUESTS["portfolio"][1], "symbols": ["aaa", " AAA "]}, "重複"),
        ("portfolio", {**REQUESTS["portfolio"][1], "rebalance_every": 0}, "以上"),
        ("portfolio", {**REQUESTS["portfolio"][1], "rebalance_threshold_pct": -0.1}, "以上"),
        ("walkforward", {**REQUESTS["walkforward"][1], "folds": 1}, "分割数"),
        ("walkforward", {**REQUESTS["walkforward"][1], "folds": 10}, "不足"),
        ("walkforward", {**REQUESTS["walkforward"][1], "folds": 6, "grid": {"period": list(range(1, 101))}}, "500"),
    ]:
        response = client.post(f"/backtest/{endpoint}", headers=headers, json=body)
        assert response.status_code == 422 and message in response.text


def test_portfolio_loading_is_bounded_and_symbol_errors_are_named(client, users, history, monkeypatch):
    active = set()
    maximum = 0

    async def load(symbol, db, *args, **kwargs):
        nonlocal maximum
        assert db not in active
        active.add(db)
        maximum = max(maximum, len(active))
        try:
            await asyncio.sleep(0.01)
            if symbol == "MISSING":
                raise SymbolNotFoundError(symbol)
            if symbol == "EMPTY":
                return {"history": []}
            return await history(symbol, db, *args, **kwargs)
        finally:
            active.remove(db)

    monkeypatch.setattr(stock_service, "get_history", load)
    for symbol in ("MISSING", "EMPTY"):
        response = client.post("/backtest/portfolio", headers=users[0]["headers"],
                               json={**BASE, "symbols": ["A", "B", "C", symbol, "D"]})
        assert response.status_code == 422 and f"{symbol} の" in response.json()["detail"]
    assert maximum == 3 and not active


def test_advanced_endpoints_share_the_user_slot(client, users, history, monkeypatch):
    entered, release = Event(), Event()
    real = backtest.run_portfolio

    def blocking(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return real(*args, **kwargs)

    monkeypatch.setattr(backtest, "run_portfolio", blocking)
    headers = users[0]["headers"]
    with ThreadPoolExecutor() as pool:
        pending = pool.submit(client.post, "/backtest/portfolio", headers=headers, json=REQUESTS["portfolio"][1])
        try:
            assert entered.wait(5)
            for endpoint in ("portfolio", "walkforward"):
                response = client.post(f"/backtest/{endpoint}", headers=headers, json=REQUESTS[endpoint][1])
                assert response.status_code == 429 and "上限" in response.json()["detail"]
        finally:
            release.set()
        assert pending.result(timeout=5).status_code == 200
    assert len(run_result(client, headers, "walkforward")["result"]["folds"]) == 2


def test_portfolio_downsamples_response_without_dropping_endpoints(client, users, history, monkeypatch):
    first = date(2015, 1, 1)
    rows = [{"date": (first + timedelta(days=i)).isoformat(), "close": 10} for i in range(2200)]

    async def long_history(*args, **kwargs):
        return {"history": rows}

    monkeypatch.setattr(stock_service, "get_history", long_history)
    response = client.post("/backtest/portfolio", headers=users[0]["headers"], json={
        **REQUESTS["portfolio"][1], "start_date": rows[0]["date"], "end_date": rows[-1]["date"],
    })
    assert response.status_code == 200
    curve = response.json()["equity_curve"]
    assert len(curve) == 2000
    assert curve[0]["date"] == rows[0]["date"] and curve[-1]["date"] == rows[-1]["date"]
    assert curve[-1]["holdings"] == {"AAA": 0.0, "^N225": 0.0}


def test_portfolio_rejects_mixed_cached_currencies(client, users, history, settings, monkeypatch):
    now = to_db().isoformat(" ")
    with sqlite3.connect(settings.database_url.split("///", 1)[1]) as db:
        db.execute("INSERT INTO company_profiles (symbol, name, currency, cached_at) VALUES (?, ?, ?, ?)",
                   ("7203.T", "Toyota", "JPY", now))
        db.execute("INSERT INTO stock_prices (symbol, interval, timestamp, close, quote_currency, cached_at) "
                   "VALUES (?, 'quote', ?, ?, ?, ?)", ("AAPL", now, 200, "USD", now))
    monkeypatch.setattr(stock_service, "get_current_price", AsyncMock(side_effect=AssertionError("quote already cached")))
    monkeypatch.setattr(profile_service, "get_profile", AsyncMock(side_effect=AssertionError("profile already cached")))
    response = client.post("/backtest/portfolio", headers=users[0]["headers"],
                           json={**BASE, "symbols": ["7203.T", "AAPL"]})
    assert response.status_code == 422
    assert response.json()["detail"] == "通貨が異なる銘柄（JPY: 7203.T / USD: AAPL）は同じポートフォリオで検証できません"


def test_portfolio_warns_when_neither_currency_service_can_identify_a_symbol(client, users, history, monkeypatch):
    async def quote(symbol, db):
        if symbol == "7203.T":
            raise UpstreamUnavailableError
        return {"currency": "JPY"}

    get_profile = AsyncMock(return_value={"currency": None})
    monkeypatch.setattr(stock_service, "get_current_price", quote)
    monkeypatch.setattr(profile_service, "get_profile", get_profile)
    response = client.post("/backtest/portfolio", headers=users[0]["headers"],
                           json={**BASE, "symbols": ["7203.T", "6758.T"], "rebalance_every": 4})
    assert response.status_code == 200
    get_profile.assert_awaited_once()
    result = response.json()
    assert any("7203.T の通貨を確認できなかった" in item for item in result["warnings"])
    assert all(trade["reason"] in {"signal", "rebalance"} for trade in result["trades"])


def test_walkforward_fold_warning_is_serialized(client, users, history, monkeypatch):
    async def flat_prices(*args, **kwargs):
        return {"history": [{"date": f"2024-01-{day:02}", "close": 10} for day in range(1, 14)]}

    monkeypatch.setattr(stock_service, "get_history", flat_prices)
    folds = run_result(client, users[0]["headers"], "walkforward")["result"]["folds"]
    assert all(fold["is_metric"] is None and "先頭のパラメータ" in fold["warning"] for fold in folds)


def test_saved_runs_crud_auth_ownership_and_client_summary(client, users, history):
    alice, bob = [row["headers"] for row in users]
    assert client.get("/backtest/runs").status_code == 401
    assert client.post("/backtest/runs", json={}).status_code == 401
    ids = []
    for kind in REQUESTS:
        body = run_result(client, alice, kind)
        if kind == "single":
            body["result"]["metrics"]["return_pct"] = 123.45
        response = client.post("/backtest/runs", headers=alice, json=body)
        assert response.status_code == 201, response.text
        info = response.json()
        ids.append(info["id"])
        assert info["metrics_source"] == "client"
        assert "request" not in info and "result" not in info
        full = client.get(f"/backtest/runs/{info['id']}", headers=alice).json()
        assert full["result"] == body["result"]
        assert full["request"]["params"] == {"period": 1, "threshold": 0.0, "exit_threshold": 0.0, "max_per": None}
        expected_label = {
            "single": "AAA momentum", "grid": "AAA momentum グリッド探索",
            "portfolio": "ポートフォリオ 2銘柄 momentum", "walkforward": "AAA momentum ウォークフォワード",
        }[kind]
        assert full["name"].rsplit(" ", 1)[0] == expected_label
        if kind == "single":
            assert full["request"]["symbol"] == "AAA"
            assert full["summary"]["return_pct"] == 123.45
    listing = client.get("/backtest/runs", headers=alice).json()["runs"]
    assert [row["id"] for row in listing] == list(reversed(ids))
    assert all("result" not in row for row in listing)
    assert client.get("/backtest/runs", headers=bob).json() == {"runs": []}
    path = f"/backtest/runs/{ids[0]}"
    for method, kwargs in [("GET", {}), ("PATCH", {"json": {"name": "変更"}}), ("DELETE", {})]:
        assert client.request(method, path, headers=bob, **kwargs).status_code == 404
    assert client.patch(path, headers=alice, json={"name": " "}).status_code == 422
    edited = client.patch(path, headers=alice, json={"name": " 変更 ", "note": "テスト"}).json()
    assert (edited["name"], edited["note"]) == ("変更", "テスト")
    assert client.patch(path, headers=alice, json={"note": None}).json()["note"] is None
    assert client.delete(path, headers=alice).status_code == 204
    assert client.get(path, headers=alice).status_code == 404


def test_saved_run_limits_and_request_revalidation(client, users, history, settings):
    headers = users[0]["headers"]
    body = run_result(client, headers)
    result = body["result"]
    result["equity_curve"] = [dict(date=(date(2020, 1, 1) + timedelta(days=i)).isoformat(),
                                   equity=100 + i, benchmark_equity=100) for i in range(2001)]
    result["trades"] *= 1500
    settings.backtest_max_saved_runs = 1
    rejected = client.post("/backtest/runs", headers=headers, json={**body, "request": {**BASE, "symbol": "A/B"}})
    assert rejected.status_code == 422
    size_error = client.post("/backtest/runs", headers=headers,
                             json={**body, "result": {**result, "warnings": ["x" * (512 * 1024)]}})
    assert size_error.status_code == 413 and "512 KiB" in size_error.json()["detail"]
    accepted = client.post("/backtest/runs", headers=headers, json=body)
    assert accepted.status_code == 201, accepted.text
    saved = client.get(f"/backtest/runs/{accepted.json()['id']}", headers=headers).json()["result"]
    assert len(saved["equity_curve"]) == 500 and len(saved["trades"]) == 1000
    assert saved["equity_curve"][0] == result["equity_curve"][0]
    assert saved["equity_curve"][-1] == result["equity_curve"][-1]
    response = client.post("/backtest/runs", headers=headers, json=body)
    assert response.status_code == 409 and "1 件まで" in response.json()["detail"]


def test_backup_roundtrip_legacy_replace_and_account_deletion(client, users, history, settings):
    alice, bob = users
    body = run_result(client, alice["headers"], "portfolio")
    response = client.post("/backtest/runs", headers=alice["headers"], json={**body, "note": "記録"})
    assert response.status_code == 201
    backup = client.get(f"/user/{alice['id']}/export", headers=alice["headers"]).json()
    assert backup["version"] == 4 and len(backup["backtest_runs"]) == 1
    path = f"/user/{bob['id']}/import"
    restored = client.post(path + "?mode=replace", headers=bob["headers"], json=backup).json()
    assert restored["imported"]["backtest_runs"] == 1
    again = client.post(path, headers=bob["headers"], json=backup).json()
    assert again["imported"]["backtest_runs"] == 0
    for version, omitted in [(2, {"backtest_runs"}), (1, {"backtest_runs", "holdings", "alerts"})]:
        legacy = {key: value for key, value in backup.items() if key not in omitted}
        legacy["version"] = version
        legacy["lists"] = [{"name": lst["name"], "items": []} for lst in backup["lists"]]
        if version == 2:
            legacy["holdings"] = []
        assert client.post(path + "?mode=replace", headers=bob["headers"], json=legacy).status_code == 200
    exported = client.get(f"/user/{bob['id']}/export", headers=bob["headers"]).json()
    assert exported["backtest_runs"] == backup["backtest_runs"]
    deleted = client.request("DELETE", f"/user/{alice['id']}", headers=alice["headers"],
                             json={"current_password": "password123"})
    assert deleted.status_code == 204
    with sqlite3.connect(settings.database_url.split("///", 1)[1]) as conn:
        assert conn.execute("SELECT user_id FROM backtest_runs").fetchall() == [(bob["id"],)]
