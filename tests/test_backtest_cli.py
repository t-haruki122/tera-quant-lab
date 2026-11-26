import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest
import requests

from scripts import backtest_cli as cli


def args(monkeypatch, *options):
    monkeypatch.setattr(sys, "argv", ["backtest_cli.py", "--start-date", "2024-01-01", "--end-date", "2024-12-31", *options])
    return cli.parse_args()


@pytest.fixture
def api(monkeypatch):
    client = cli.APIClient("http://fake", max_retries=3, retry_wait=0.1)
    client.session = Mock(headers={})
    monkeypatch.delenv("TQL_PASSWORD", raising=False)
    monkeypatch.delenv("TQL_TOKEN", raising=False)
    monkeypatch.setattr(cli.getpass, "getpass", Mock(side_effect=AssertionError("Unexpected password prompt")))
    return client


def response(status, *, headers=None):
    return SimpleNamespace(status_code=status, text=f"body for {status}", headers=headers or {},
                           json=lambda: {"detail": "server detail"} if status >= 400 else {"ok": True})


@pytest.fixture
def sleep(monkeypatch):
    mocked = Mock()
    monkeypatch.setattr(cli.time, "sleep", mocked)
    return mocked


def test_sma_crossover_trades_fees_and_mark_to_market():
    prices = [3, 2, 1, 2, 3, 4, 3, 2, 1, 2, 3, 4]
    df = pd.DataFrame({"date": [f"2024-01-{i:02d}" for i in range(1, 13)], "close": prices})
    result = cli.Backtester(cli.SmaCrossPerStrategy(2, 3), 1000, fee_rate=0.01, execution="close").run("TEST", df)
    # Buy 330*3 + 9.90 fee => cash 0.10; sell 330*2 - 6.60 fee
    # => 653.50; buy 215*3 + 6.45 fee => 2.05 cash; hold 215 at 4.
    assert [{k: round(v, 4) if isinstance(v, float) else v for k, v in t.items()
             if k not in ('signal_date', 'slippage', 'open_fallback')} for t in result.trades] == [
        dict(symbol="TEST", date="2024-01-05", side="BUY", price=3, shares=330, fee=9.9, pnl=None, cash_after=0.1),
        dict(symbol="TEST", date="2024-01-08", side="SELL", price=2, shares=330, fee=6.6, pnl=-346.5, cash_after=653.5),
        dict(symbol="TEST", date="2024-01-11", side="BUY", price=3, shares=215, fee=6.45, pnl=None, cash_after=2.05),
    ]
    assert result.equity_curve["equity"].tolist() == pytest.approx(
        [1000, 1000, 1000, 1000, 990.1, 1320.1, 990.1, 653.5, 653.5, 653.5, 647.05, 862.05])
    assert result.metrics.final_asset == pytest.approx(862.05)
    assert result.metrics.return_pct == pytest.approx(-13.795)
    assert result.metrics.total_trades == 1
    assert result.metrics.win_rate == 0
    assert result.metrics.max_drawdown == pytest.approx((1320.1 - 647.05) / 1320.1 * 100)


def test_empty_history_insufficient_data_and_per_filter():
    strategy = cli.SmaCrossPerStrategy(2, 3, max_per=20)
    backtester = cli.Backtester(strategy, 1000)
    with pytest.raises(ValueError, match="株価データがありません"):
        backtester.run("X", pd.DataFrame())
    short = pd.DataFrame({"date": ["2024-01-01"], "close": [10]})
    assert backtester.run("X", short).metrics.final_asset == 1000
    df = pd.DataFrame({"date": [f"2024-01-{i:02d}" for i in range(1, 7)], "close": [3, 2, 1, 2, 3, 4]})
    for context in ({}, {"pe_ratio": 21}):
        assert backtester.run("X", df, context).trades == []
    assert backtester.run("X", df, {"financial_history": {"history": [{"period": "2022", "eps": 1}]}}).trades[0]["side"] == "BUY"


@pytest.mark.parametrize("windows", [(0, 3), (2, -1), (3, 3), (4, 3)])
def test_invalid_sma_windows(windows):
    with pytest.raises(ValueError):
        cli.SmaCrossPerStrategy(*windows)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_client_does_not_retry_4xx(api, sleep, status):
    api.session.request.return_value = response(status)
    with pytest.raises(RuntimeError) as error:
        api._request("POST", "/test")
    api.session.request.assert_called_once()
    sleep.assert_not_called()
    if status in (401, 403):
        assert "server detail" in str(error.value)
    if status == 404:
        assert str(error.value) == "404 Not Found: http://fake/test"


@pytest.mark.parametrize("status", [401, 403])
def test_non_json_auth_error(api, sleep, status):
    api.session.request.return_value = SimpleNamespace(status_code=status, text="plain error",
                                                        json=Mock(side_effect=ValueError("invalid JSON")))
    with pytest.raises(RuntimeError, match="plain error"):
        api._request("GET", "/test")
    api.session.request.assert_called_once()
    sleep.assert_not_called()


@pytest.mark.parametrize("failure", [429, 500, 503, requests.Timeout("transient"), requests.ConnectionError("transient")])
def test_transient_errors_retry(api, sleep, failure):
    api.session.request.side_effect = [response(failure) if isinstance(failure, int) else failure, response(200)]
    assert api._request("GET", "/test") == {"ok": True}
    assert api.session.request.call_count == 2
    assert sum(call.args[0] for call in sleep.call_args_list) == pytest.approx(0.1)


@pytest.mark.parametrize("header,expected", [("2", 2), ("999", 30), ("0", 0), ("invalid", 0.7),
                                            ("NaN", 0.7), ("Infinity", 0.7), ("-1", 0.7), (None, 0.7)])
def test_retry_after_is_bounded_and_chunked(api, sleep, header, expected):
    api.retry_wait = 0.7
    headers = {} if header is None else {"Retry-After": header}
    api.session.request.side_effect = [response(429, headers=headers), response(200)]
    assert api._request("GET", "/test") == {"ok": True}
    assert api.session.request.call_count == 2
    assert sum(call.args[0] for call in sleep.call_args_list) == pytest.approx(expected)
    assert all(0 < call.args[0] <= 0.1 for call in sleep.call_args_list)


def test_retry_exhaustion(api, sleep):
    api.session.request.return_value = response(500)
    with pytest.raises(RuntimeError, match="API request failed after retries:"):
        api._request("GET", "/test")
    assert api.session.request.call_count == 3
    assert sum(call.args[0] for call in sleep.call_args_list) == pytest.approx(0.3)


def test_cancellation_before_request(api, sleep):
    with pytest.raises(RuntimeError, match="^Cancelled$"):
        api._request("GET", "/test", cancel_check=lambda: True)
    api.session.request.assert_not_called()
    sleep.assert_not_called()


def test_cancellation_during_retry_after(api, sleep):
    api.session.request.return_value = response(429, headers={"Retry-After": "2"})
    with pytest.raises(RuntimeError, match="^Cancelled$"):
        api._request("GET", "/test", cancel_check=lambda: sleep.call_count >= 3)
    api.session.request.assert_called_once()
    assert sleep.call_count == 3


def test_cancellation_raised_by_transport(api, sleep):
    api.session.request.side_effect = RuntimeError("Cancelled")
    with pytest.raises(RuntimeError, match="^Cancelled$"):
        api._request("GET", "/test")
    api.session.request.assert_called_once()
    sleep.assert_not_called()


@pytest.mark.parametrize("use_env", [False, True])
def test_username_auth_infers_user_id(api, monkeypatch, use_env):
    options = args(monkeypatch, "--watchlist-id", "2", "--username", "alice")
    monkeypatch.setenv("TQL_TOKEN", "ignored-because-username-is-explicit")
    if use_env:
        monkeypatch.setenv("TQL_PASSWORD", "password123")
    else:
        monkeypatch.setattr(cli.getpass, "getpass", Mock(return_value="password123"))
    api.session.request.side_effect = [SimpleNamespace(status_code=200, json=lambda: {
        "id": 7, "access_token": "login-token"}), SimpleNamespace(status_code=200, json=lambda: {
            "items": [{"symbol": "msft"}, {"symbol": "AAPL"}, {"symbol": "msft"}, {}]})]
    cli.validate_args(options)
    assert cli.resolve_symbols(api, options) == ["AAPL", "MSFT"]
    assert api.session.request.call_args_list[0].kwargs["json"] == {"username": "alice", "password": "password123"}
    assert api.session.request.call_args.kwargs["url"] == "http://fake/user/7/lists/2"
    assert api.session.headers["Authorization"] == "Bearer login-token"
    if use_env:
        cli.getpass.getpass.assert_not_called()
    else:
        cli.getpass.getpass.assert_called_once_with("Password: ")


@pytest.mark.parametrize("user_id", [7, 8])
def test_username_explicit_id_match_and_mismatch(api, monkeypatch, user_id):
    options = args(monkeypatch, "--watchlist-id", "2", "--username", "alice", "--user-id", str(user_id))
    monkeypatch.setenv("TQL_PASSWORD", "password123")
    api.login = Mock(return_value={"id": 7})
    api.get_watchlist_symbols = Mock(return_value=["AAPL"])
    if user_id == 8:
        with pytest.raises(ValueError, match="一致しません"):
            cli.resolve_symbols(api, options)
        api.get_watchlist_symbols.assert_not_called()
    else:
        assert cli.resolve_symbols(api, options) == ["AAPL"]
        api.get_watchlist_symbols.assert_called_once_with(7, 2)


@pytest.mark.parametrize("source", ["flag", "env"])
def test_token_auth_and_required_user_id(api, monkeypatch, source):
    extra = ["--token", "flag-token"] if source == "flag" else []
    monkeypatch.setenv("TQL_TOKEN", "env-token")
    options = args(monkeypatch, "--watchlist-id", "2", *extra)
    with pytest.raises(ValueError, match="--user-id"):
        cli.validate_args(options)
    options.user_id = 7
    cli.validate_args(options)
    api.get_watchlist_symbols = Mock(return_value=["AAPL"])
    assert cli.resolve_symbols(api, options) == ["AAPL"]
    assert api.session.headers["Authorization"] == f"Bearer {source}-token"
    cli.getpass.getpass.assert_not_called()


def test_watchlist_missing_credentials_and_empty_list(api, monkeypatch):
    options = args(monkeypatch, "--watchlist-id", "2", "--user-id", "7")
    with pytest.raises(ValueError, match="TQL_TOKEN"):
        cli.resolve_symbols(api, options)
    options.token = "token"
    api.get_watchlist_symbols = Mock(return_value=[])
    with pytest.raises(ValueError, match="銘柄が存在しません"):
        cli.resolve_symbols(api, options)


def test_single_symbol_needs_no_auth(api, monkeypatch):
    options = args(monkeypatch, "--symbol", "msft")
    cli.validate_args(options)
    assert cli.resolve_symbols(api, options) == ["MSFT"]
    cli.getpass.getpass.assert_not_called()
    api.session.request.assert_not_called()
    assert "Authorization" not in api.session.headers


@pytest.mark.parametrize("field,value,message", [
    ("start_date", "bad", "YYYY-MM-DD"), ("end_date", "2024-13-01", "YYYY-MM-DD"),
    ("start_date", "2025-01-01", "end-date 以前"),
    ("initial_capital", 0, "initial-capital"), ("initial_capital", -1, "initial-capital"),
    ("fee_rate", -0.1, "fee-rate"),
])
def test_validate_args(api, monkeypatch, field, value, message):
    options = args(monkeypatch, "--symbol", "AAPL")
    setattr(options, field, value)
    with pytest.raises(ValueError, match=message):
        cli.validate_args(options)


def test_gui_namespace_still_supports_unauthenticated_run(api, monkeypatch):
    # GUI shares CLI functions but its Namespace deliberately has no auth fields.
    options = SimpleNamespace(symbol="AAPL", start_date="2024-01-01", end_date="2024-12-31", interval="1d",
                              initial_capital=1000, fee_rate=0, short_window=2, long_window=3, max_per=None,
                              fetch_financial_history=False)
    cli.validate_args(options)
    assert cli.resolve_symbols(api, options) == ["AAPL"]
    api.session.request.return_value = SimpleNamespace(status_code=200, json=lambda: {"history": [
        dict(date=f"2024-01-{i:02d}", open=10, high=12, low=9, close=11, volume=100) for i in range(1, 6)]})
    progress = Mock()
    result = cli.run_single_symbol(api, "AAPL", options, 1000, threading.Event(), progress)
    assert result.metrics.final_asset == 1000
    assert "Authorization" not in api.session.headers
    assert progress.call_args.args[0] == 100
