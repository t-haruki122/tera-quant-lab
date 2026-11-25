import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import AsyncMock

import pytest

from app.exceptions import UPSTREAM_UNAVAILABLE, UpstreamUnavailableError
from app.routers import backtest
from app.services import finance_service, stock_service

BODY = {
    "symbol": " ^n225 ", "start_date": "2024-01-02", "end_date": "2024-01-06",
    "strategy": "momentum", "params": {"period": 1}, "execution": "close",
}


@pytest.fixture
def services(monkeypatch):
    prices = [9, 10, 20, 15, 9, 8]
    history = AsyncMock(return_value={"history": [
        {"date": f"2024-01-0{idx}", "open": price, "close": price}
        for idx, price in enumerate(prices, 1)
    ]})
    eps = AsyncMock(return_value={"history": []})
    monkeypatch.setattr(stock_service, "get_history", history)
    monkeypatch.setattr(finance_service, "get_financial_history", eps)
    return history, eps


def test_public_registry_and_protected_jobs(client, services):
    response = client.get("/backtest/strategies")
    assert response.status_code == 200
    schema = {item["name"]: item["params"] for item in response.json()["strategies"]}
    assert schema["sma_cross"][0] == dict(
        name="short_window", type="int", default=20, min=1, description="短期移動平均のバー数",
    )
    choices = next(field for field in schema["bollinger"] if field["name"] == "exit")
    assert choices["choices"] == ["middle", "upper"]
    assert "sharpe" in response.json()["rank_metrics"]
    for path, body in [("run", BODY), ("grid", {**BODY, "grid": {}})]:
        denied = client.post(f"/backtest/{path}", json=body)
        assert denied.status_code == 401
        assert denied.headers["WWW-Authenticate"] == "Bearer"
    services[0].assert_not_called()


def test_backtest_routes_use_generic_upstream_errors(client, users, services):
    raw = "private backtest upstream response"
    services[0].side_effect = UpstreamUnavailableError(raw)
    for path, body in [("run", BODY), ("grid", {**BODY, "grid": {}})]:
        response = client.post(f"/backtest/{path}", headers=users[0]["headers"], json=body)
        assert response.status_code == 503
        assert response.json() == {"detail": UPSTREAM_UNAVAILABLE, "status_code": 503}
        assert raw not in response.text


@pytest.mark.parametrize(("changes", "message"), [
    ({"start_date": "bad"}, "YYYY-MM-DD"),
    ({"end_date": "2024-02-30"}, "date"),
    ({"end_date": "2023-12-31"}, "start_date は end_date 以前"),
    ({"start_date": "2000-01-01"}, "最大 20 年"),
    ({"interval": "1h"}, "1d、1wk、1mo のいずれか"),
    ({"symbol": "bad/path"}, "symbol"),
    ({"strategy": "missing"}, "Unknown strategy"),
    ({"params": {"unspecified": 1}}, "Unknown params"),
    ({"params": {"period": 0}}, "period must be positive"),
    ({"fee_rate": 0.11}, "0.1"),
    ({"slippage_bps": -1}, "greater than or equal to 0"),
    ({"initial_capital": 0}, "greater than 0"),
    ({"eps_lag_days": 731}, "730"),
    ({"risk_free": 2}, "less than or equal to 1"),
])
def test_validation(client, users, services, changes, message):
    response = client.post("/backtest/run", headers=users[0]["headers"], json={**BODY, **changes})
    assert response.status_code == 422
    assert message in " ".join(error["msg"] for error in response.json()["detail"])
    services[0].assert_not_called()


def test_override_calendar_span_and_nonfinite_input(client, users, monkeypatch, settings, services):
    monkeypatch.setattr(settings, "backtest_max_span_years", 1)
    headers = users[0]["headers"]
    invalid = client.post("/backtest/run", headers=headers, json={**BODY, "start_date": "2020-02-29"})
    assert invalid.status_code == 422 and "最大 1 年" in invalid.json()["detail"][0]["msg"]
    accepted = client.post("/backtest/run", headers=headers, json={
        **BODY, "start_date": "2024-02-29", "end_date": "2025-02-28",
    })
    assert accepted.status_code == 422 and "No price data in the requested period" in accepted.json()["detail"]
    nonfinite = client.post("/backtest/run", headers={**headers, "Content-Type": "application/json"},
                            content='{"symbol":"X","start_date":"2024-01-01","end_date":"2024-01-02","initial_capital":1e500}')
    assert nonfinite.status_code == 422
    assert nonfinite.json()["detail"][0]["input"] is None


def test_run_warmup_benchmark_trades_eps_and_json_safety(client, users, services, monkeypatch):
    history, eps = services
    original_run = backtest.Backtester.run

    def nonfinite_run(*args, **kwargs):
        result = original_run(*args, **kwargs)
        result.benchmark_metrics.sortino = float("nan")
        return result

    monkeypatch.setattr(backtest.Backtester, "run", nonfinite_run)
    response = client.post("/backtest/run", headers=users[0]["headers"], json=BODY)
    assert response.status_code == 200
    result = response.json()
    assert set(result) == {"metrics", "benchmark_metrics", "equity_curve", "trades", "warnings"}
    assert result["metrics"]["symbol"] == "^N225"
    assert result["metrics"]["profit_factor"] is None  # A win with no losing trade produces infinity.
    assert result["benchmark_metrics"]["sortino"] is None
    assert result["benchmark_metrics"]["exposure"] == 100
    assert result["equity_curve"][0] == {"date": "2024-01-02", "equity": 1e6, "benchmark_equity": 1e6}
    assert [trade["side"] for trade in result["trades"]] == ["BUY", "SELL"]
    assert result["trades"][0]["date"] == "2024-01-02"  # The signal uses the previous warm-up bar.
    assert history.call_args.args[0] == "^N225"
    assert history.call_args.kwargs["warmup_days"] > 1
    eps.assert_not_called()

    filtered = client.post("/backtest/run", headers=users[0]["headers"], json={
        **BODY, "params": {"period": 1, "max_per": 20},
    }).json()
    assert filtered["trades"] == []
    assert filtered["metrics"]["missing_eps_bars"] == 5
    assert "Historical EPS" in filtered["warnings"][0]
    eps.assert_awaited_once()


def test_grid_limits_validation_ranking_and_data_reuse(client, users, services, settings, monkeypatch):
    headers = users[0]["headers"]
    oversize = client.post("/backtest/grid", headers=headers, json={**BODY, "grid": {"threshold": [0] * 101}})
    assert oversize.status_code == 422 and "maximum is 100" in oversize.json()["detail"][0]["msg"]
    monkeypatch.setattr(settings, "backtest_grid_max_combos", 2)
    for grid, rank in [({"threshold": [0, 1, 2]}, "sharpe"), ({"unknown": [1]}, "sharpe"),
                       ({"threshold": []}, "sharpe"), ({}, "bad")]:
        denied = client.post("/backtest/grid", headers=headers, json={**BODY, "grid": grid, "rank_by": rank})
        assert denied.status_code == 422
    history, eps = services
    history.assert_not_called()

    def post(grid):
        response = client.post("/backtest/grid", headers=headers, json={**BODY, "grid": grid, "rank_by": "return_pct"})
        assert response.status_code == 200
        return response.json()

    result = post({"threshold": [5, 0]})
    assert result["rank_by"] == "return_pct"
    assert [row["rank"] for row in result["results"]] == [1, 2]
    assert [row["params"]["threshold"] for row in result["results"]] == [0, 5]
    assert all(set(row) == {"rank", "params", "metrics"} for row in result["results"])
    history.assert_awaited_once()
    eps.assert_not_called()
    post({"max_per": [None]})
    eps.assert_not_called()
    post({"max_per": [None, 25]})
    assert history.await_count == 3
    eps.assert_awaited_once()


@pytest.mark.parametrize("endpoint", ["run", "grid"])
def test_timed_out_worker_observes_cancel_and_slot_is_reused(client, users, settings, services, monkeypatch, endpoint):
    monkeypatch.setattr(settings, "backtest_timeout_seconds", .15)
    entered, cancelled = Event(), Event()
    original_run = backtest.Backtester.run

    def stalled(*args, cancel_check, **kwargs):
        entered.set()
        for _ in range(500):
            if cancel_check():
                cancelled.set()
                raise RuntimeError("Cancelled")
            cancelled.wait(.01)
        raise AssertionError("Cancellation flag remained unset")

    monkeypatch.setattr(backtest.Backtester, "run", stalled)
    headers = users[0]["headers"]
    body = {**BODY, "grid": {}} if endpoint == "grid" else BODY
    timed_out = client.post(f"/backtest/{endpoint}", headers=headers, json=body)
    assert entered.is_set() and timed_out.status_code == 504
    assert "制限時間" in timed_out.json()["detail"]
    assert cancelled.wait(3)
    monkeypatch.setattr(backtest.Backtester, "run", original_run)
    assert client.post(f"/backtest/{endpoint}", headers=headers, json=body).status_code == 200


def test_timeout_includes_service_and_external_cancellation_releases_slot(client, users, settings, services, monkeypatch):
    monkeypatch.setattr(settings, "backtest_timeout_seconds", .1)
    cancelled = Event()

    async def stalled(*args, **kwargs):
        try:
            await asyncio.sleep(30)
        finally:
            cancelled.set()

    services[0].side_effect = stalled
    assert client.post("/backtest/run", headers=users[0]["headers"], json=BODY).status_code == 504
    assert cancelled.is_set()
    services[0].side_effect = None
    monkeypatch.setattr(settings, "backtest_timeout_seconds", 3)

    async def cancel_request_task():
        services[0].side_effect = stalled
        request = asyncio.create_task(backtest._run_job(backtest.RunRequest(**BODY), users[0]["id"], None))
        await asyncio.sleep(0)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        services[0].side_effect = None

    asyncio.run(cancel_request_task())
    assert client.post("/backtest/run", headers=users[0]["headers"], json=BODY).status_code == 200


def test_per_user_concurrency_across_routes_and_release_after_error(client, users, services, monkeypatch):
    entered, release = Event(), Event()
    original_run = backtest.Backtester.run

    def blocking_error(self, symbol, *args, **kwargs):
        if symbol == "HOLD":
            entered.set()
            assert release.wait(10)
            raise ValueError("test engine error")
        return original_run(self, symbol, *args, **kwargs)

    monkeypatch.setattr(backtest.Backtester, "run", blocking_error)
    alice, bob = [user["headers"] for user in users]
    with ThreadPoolExecutor(max_workers=1) as pool:
        slow = pool.submit(client.post, "/backtest/run", headers=alice, json={**BODY, "symbol": "HOLD"})
        try:
            assert entered.wait(5)
            limited = client.post("/backtest/grid", headers=alice, json={**BODY, "grid": {}})
            assert limited.status_code == 429 and int(limited.headers["Retry-After"]) >= 1
            assert "上限" in limited.json()["detail"]
            assert client.post("/backtest/run", headers=bob, json=BODY).status_code == 200
        finally:
            release.set()
        assert slow.result(timeout=5).json() == {"detail": "test engine error"}
    assert client.post("/backtest/run", headers=alice, json=BODY).status_code == 200
