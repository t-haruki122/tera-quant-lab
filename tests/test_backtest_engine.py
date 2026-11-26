"""Small arithmetic fixtures; no live transport or production database access."""

import dataclasses
import math
import os
import subprocess
import sys
from unittest.mock import Mock

import pandas as pd
import pytest

from app.backtest import (
    Backtester,
    Strategy,
    calculate_metrics,
    grid_params,
    history_to_df,
    make_strategy,
    run_grid,
    validate_params,
)
from app.backtest.pit import annual_eps
from scripts import backtest_cli as cli


class Signals(Strategy):
    def __init__(self, signals):
        self.signals = signals

    def prepare(self, price_df, context):
        return price_df.assign(signal=self.signals)


def frame(closes, opens=None, dates=None):
    return pd.DataFrame(dict(date=dates if dates is not None else pd.date_range("2024-01-01", periods=len(closes)).strftime("%Y-%m-%d"),
                             close=closes, open=opens if opens is not None else closes))


def test_next_open_equity_last_signal_and_close_mode():
    prices = frame([10, 15, 20, 25], [9, 12, 18, 24])
    # t0 buy signal; t1 buy 8 at 12, cash 4; t2 sell at 18, cash 148.
    # t3 buy signal has no t4 and is discarded.
    result = Backtester(Signals([1, -1, 0, 1]), 100).run("X", prices)
    assert [(t["date"], t["side"], t["price"]) for t in result.trades] == [
        ("2024-01-02", "BUY", 12), ("2024-01-03", "SELL", 18)]
    assert result.equity_curve.equity.tolist() == [100, 124, 148, 148]
    assert result.trades[0]["signal_date"] == "2024-01-01"
    close = Backtester(Signals([1, -1, 0, 1]), 100, execution="close").run("X", prices)
    assert [(t["side"], t["price"]) for t in close.trades] == [("BUY", 10), ("SELL", 15), ("BUY", 25)]
    assert close.equity_curve.equity.tolist() == [100, 150, 150, 150]


def test_fractional_price_integer_sizing_and_single_bar_benchmark():
    # 100 / 0.1 = 1000 affordable whole shares (float // can incorrectly yield 999).
    progress = Mock()
    result = Backtester(Signals([1, 0]), 100).run("X", frame([.2, .3], [.2, .1]), progress_callback=progress)
    assert result.trades[0]["shares"] == 1000
    assert result.metrics.final_asset == pytest.approx(300)
    assert progress.call_args.args[2]["price"] == .1  # Log actual fill, not bar close .3.
    one = Backtester(Signals([1]), 100).run("X", frame([10]))
    assert not one.trades and one.benchmark_curve.equity.tolist() == [100]
    assert one.metrics.benchmark_return_pct == 0


def test_slippage_fees_and_fallback_counts():
    # Buy 8 at 11 (+10%), fee 8.8, cash 3.2. Sell at 18 (-10%),
    # fee 14.4, net proceeds 129.6 => 132.8, realised profit 32.8.
    result = Backtester(Signals([1, -1, 0]), 100, fee_rate=.1, slippage_bps=1000).run(
        "X", frame([10, 10, 20], [10, None, None]))
    assert result.equity_curve.equity.tolist() == pytest.approx([100, 83.2, 132.8])
    assert result.trades[0]["price"] == 11
    assert result.trades[1]["price"] == 18
    assert result.trades[1]["pnl"] == pytest.approx(32.8)
    assert result.trades[0]["slippage"] == 8
    assert result.trades[1]["slippage"] == 16
    assert result.metrics.open_fallbacks == 2
    assert result.metrics.benchmark_open_fallbacks == 1
    assert "戦略で 2 件、買い持ちで 1 件を終値で約定" in result.warnings[0]


def test_every_metric_hand_computed():
    # Initial 100; equities 110, 99, 99. Returns +.1, -.1, 0;
    # mean=0, population variance=.02/3; monthly annualisation=12.
    # rf=1.01**12-1 => per-bar rf=.01; excess=.09,-.11,-.01.
    # downside RMS=sqrt((.11**2+.01**2)/3); mean excess=-.01.
    # Closed PnLs +10,-5,0: win 1/3, PF=10/5, averages +10,-5.
    curve = pd.DataFrame(dict(equity=[110, 99, 99], shares=[1, 1, 0]))
    trades = [dict(side="SELL", pnl=p) for p in [10, -5, 0]]
    m = calculate_metrics("X", curve, trades, 100, "1mo", 1.01 ** 12 - 1)
    expected = dict(symbol="X", initial_capital=100, final_asset=99, return_pct=-1,
                    cagr=(.99 ** 4 - 1) * 100, annualised_volatility=math.sqrt(.08) * 100,
                    sharpe=-.01 / math.sqrt(.02 / 3) * math.sqrt(12),
                    sortino=-.01 / math.sqrt(.0122 / 3) * math.sqrt(12),
                    total_trades=3, win_rate=100 / 3, profit_factor=2,
                    average_win=10, average_loss=-5, exposure=200 / 3,
                    max_drawdown=10, max_drawdown_duration=2,
                    open_fallbacks=0, missing_eps_bars=0, benchmark_return_pct=0,
                    benchmark_cagr=0, benchmark_max_drawdown=0, benchmark_open_fallbacks=0)
    for key, value in expected.items():
        assert getattr(m, key) == (value if isinstance(value, str) else pytest.approx(value)), key
    assert set(expected) == set(dataclasses.asdict(m))


@pytest.mark.parametrize("interval,factor", [("1d", 252), ("1wk", 52), ("1mo", 12)])
def test_annualisation_and_initial_cost_drawdown(interval, factor):
    # Returns -.1, 0; mean=-.05, std=.05; first bar is below INITIAL capital.
    m = calculate_metrics("X", pd.DataFrame(dict(equity=[90, 90], shares=[1, 1])), [], 100, interval)
    assert m.cagr == pytest.approx((.9 ** (factor / 2) - 1) * 100)
    assert m.annualised_volatility == pytest.approx(.05 * math.sqrt(factor) * 100)
    assert m.sharpe == pytest.approx(-math.sqrt(factor))
    assert m.max_drawdown == pytest.approx(10)
    assert m.max_drawdown_duration == 2


def test_flat_no_trades_and_no_losers_edge_cases():
    curve = pd.DataFrame(dict(equity=[100, 100], shares=[0, 0]))
    m = calculate_metrics("X", curve, [], 100)
    assert m.sharpe is m.sortino is m.profit_factor is m.average_win is m.average_loss is None
    assert (m.return_pct, m.cagr, m.annualised_volatility, m.win_rate, m.total_trades, m.exposure,
            m.max_drawdown, m.max_drawdown_duration) == (0,) * 8
    m = calculate_metrics("X", curve, [dict(side="SELL", pnl=10)], 100)
    assert m.profit_factor == math.inf and m.average_loss is None
    m = calculate_metrics("X", curve, [dict(side="SELL", pnl=-10)], 100)
    assert m.profit_factor == 0 and m.average_win is None
    # New peak/recovery resets underwater duration.
    m = calculate_metrics("X", pd.DataFrame(dict(equity=[90, 100, 80], shares=[1, 1, 1])), [], 100)
    assert m.max_drawdown_duration == 1


@pytest.mark.parametrize("execution", ["next_open", "close"])
def test_buy_and_hold_same_costs_execution(execution):
    # 10% slip and 10% fee: next_open buys floor(100/14.52)=6 at13.2,
    # cash=12.88; closes 15,20 => 102.88,132.88.
    # close buys 8 at11, fee8.8, cash3.2; closes 10,15,20 => 83.2,123.2,163.2.
    prices = frame([10, 15, 20], [9, 12, 18])
    result = Backtester(Signals([0, 0, 0]), 100, .1, execution, 1000, "1mo").run("X", prices)
    expected = [100, 102.88, 132.88] if execution == "next_open" else [83.2, 123.2, 163.2]
    assert result.benchmark_curve.equity.tolist() == pytest.approx(expected)
    ratio = expected[-1] / 100
    assert result.metrics.benchmark_return_pct == pytest.approx((ratio - 1) * 100)
    assert result.metrics.benchmark_cagr == pytest.approx((ratio ** 4 - 1) * 100)
    assert result.metrics.benchmark_max_drawdown == pytest.approx(0 if execution == "next_open" else 16.8)
    assert result.metrics.final_asset == 100


def test_pit_lag_negative_eps_missing_count_and_exits_unblocked():
    # fiscal 2022 available Jan 3 (+3d); 2023 available Jan 5 (+3d), negative.
    prices = frame([3, 2, 1, 2, 3, 2, 1], dates=pd.date_range("2024-01-01", periods=7).strftime("%Y-%m-%d"))
    context = dict(eps_lag_days=3, financial_history={"history": [
        dict(period="2022", period_end="2023-12-31", eps=1),
        dict(period="2023", period_end="2024-01-02", eps=-1),
    ]})
    strategy = make_strategy("momentum", dict(period=1, threshold=0, exit_threshold=0, max_per=20))
    prepared = strategy.prepare(prices, context)
    assert prepared.eps.iloc[:2].isna().all()
    assert prepared.eps.iloc[3] == 1
    assert prepared.eps.iloc[2] == 1
    assert prepared.signal.tolist() == [0, -1, -1, 1, 0, -1, -1]
    result = Backtester(strategy, 100, execution="close").run("X", prices, context)
    assert [t["side"] for t in result.trades] == ["BUY", "SELL"]
    assert result.metrics.missing_eps_bars == 2
    assert "7 バー中 2 バー" in result.warnings[0]
    absent = Backtester(strategy, 100).run("X", prices)
    assert not absent.trades and absent.metrics.missing_eps_bars == 7
    assert "7 バー中 7 バー" in absent.warnings[0]


def test_eps_derivation_year_fallback_and_unknown_newer_year():
    assert annual_eps({"history": [dict(period="2023", net_income=100, shares=10)]}, 0) == [(pd.Timestamp("2023-12-31"), 10)]
    assert annual_eps({"history": [dict(period="bad", eps=1), dict(period="2023", net_income=1, shares=0)]}, 0) == [
        (pd.Timestamp("2023-12-31"), None)]
    strategy = make_strategy("momentum", dict(period=1, max_per=100))
    prepared = strategy.prepare(frame([1, 2], dates=["2024-06-01", "2025-06-01"]), dict(financial_history={"history": [
        dict(period="2023", eps=10), dict(period="2024", eps=None)]}))
    assert prepared.eps.iloc[0] == 10 and pd.isna(prepared.eps.iloc[1])


@pytest.mark.parametrize("name,params,prices,expected", [
    ("sma_cross", dict(short_window=1, long_window=2), [3, 2, 1, 2, 1], [0, 0, 0, 1, -1]),
    # RSI period 2: initial 0, next Wilder gain=.5 loss=.5 => 50, next 75.
    ("rsi_reversion", dict(period=2, lower=30, upper=70), [3, 2, 1, 2, 3], [0, 0, 1, 0, -1]),
    # window3 [3,3,1]: mean 7/3, population std=sqrt(8/9); lower=1.39.
    ("bollinger", dict(window=3, std_dev=1), [3, 3, 1, 2, 3], [0, 0, 1, -1, -1]),
    ("bollinger", dict(window=3, std_dev=1, exit="upper"), [3, 3, 1, 2, 3], [0, 0, 1, 0, -1]),
    ("momentum", dict(period=1, threshold=.1, exit_threshold=-.1), [10, 12, 12, 10], [0, 1, 0, -1]),
])
def test_strategy_signals(name, params, prices, expected):
    assert make_strategy(name, params).prepare(frame(prices), {}).signal.tolist() == expected


@pytest.mark.parametrize("name,params", [
    ("sma_cross", {"bogus": 1}), ("sma_cross", {"short_window": 1.5}),
    ("sma_cross", {"short_window": 60}), ("rsi_reversion", {"lower": 80, "upper": 70}),
    ("rsi_reversion", {"period": "NaN"}), ("bollinger", {"exit": "wrong"}),
    ("bollinger", {"std_dev": float("inf")}), ("momentum", {"period": 0}),
    ("momentum", {"exit_threshold": 1}), ("momentum", {"period": True}),
])
def test_param_errors_include_types_and_defaults(name, params):
    with pytest.raises(ValueError, match="指定できるパラメータ:.*(int|float).*default="):
        validate_params(name, params)


def test_grid_ranking_cap_progress_and_cancel():
    prices = frame([10, 12, 13, 10, 9, 15])
    progress = Mock()
    ranked = run_grid("X", prices, "momentum", dict(period=1), dict(threshold=[0, .5]),
                      rank_by="return_pct", initial_capital=100, progress_callback=progress)
    # threshold 0: buy t2 at13 (7 shares), sell t4 at9 => cash72.
    # threshold .5: last-bar entry signal only => cash100, ranked first.
    assert [r.params["threshold"] for r in ranked] == [.5, 0]
    assert [r.result.metrics.return_pct for r in ranked] == pytest.approx([0, -28])
    assert progress.call_count == 2 and progress.call_args.args[:2] == (2, 2)
    assert run_grid("X", prices, "momentum", dict(period=1), dict(threshold=[.5, 0]),
                    initial_capital=100)[-1].result.metrics.sharpe is None
    lower = run_grid("X", prices, "momentum", dict(period=1), dict(threshold=[0, .5]),
                     rank_by="max_drawdown", initial_capital=100)
    assert lower[0].params["threshold"] == .5
    with pytest.raises(ValueError, match="501.*上限は 500 件"):
        run_grid("X", prices, grid=dict(short_window=list(range(501))), initial_capital=100)
    assert len(grid_params("momentum", dict(period=1), dict(threshold=[.5] * 500))) == 500
    with pytest.raises(ValueError, match="rank metric"):
        run_grid("X", prices, rank_by="fake", initial_capital=100)
    with pytest.raises(RuntimeError, match="Cancelled"):
        run_grid("X", prices, initial_capital=100, cancel_check=lambda: True)
    with pytest.raises(RuntimeError, match="Cancelled"):
        Backtester(Signals([0] * 6), 100).run("X", prices, cancel_check=lambda: True)


def test_cancel_during_single_and_grid_execution():
    cancelled = False

    def progress(*args):
        nonlocal cancelled
        cancelled = True

    with pytest.raises(RuntimeError, match="Cancelled"):
        Backtester(Signals([0, 0, 0]), 100).run("X", frame([1, 2, 3]),
                                              progress_callback=progress, cancel_check=lambda: cancelled)
    cancelled = False
    with pytest.raises(RuntimeError, match="Cancelled"):
        run_grid("X", frame([1, 2, 3]), "momentum", dict(period=1), dict(threshold=[0, 1]),
                 initial_capital=100, progress_callback=progress, cancel_check=lambda: cancelled)


def test_partial_history_open_and_validation():
    df = history_to_df({"history": [dict(date="2024-01-02", close="12"), dict(date="2024-01-01", close=10)]})
    assert df.close.tolist() == [10, 12] and df.open.isna().all()
    with pytest.raises(ValueError, match="正の数値"):
        Backtester(Signals([0]), 100).run("X", frame([0]))
    with pytest.raises(ValueError, match="昇順"):
        Backtester(Signals([0, 0]), 100).run("X", frame([1, 2], dates=["b", "a"]))


def test_engine_imports_are_presentation_and_transport_free():
    code = "import app.backtest, sys; assert not set(('argparse','matplotlib','tkinter','requests')) & sys.modules.keys()"
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)


def test_gui_import_without_display():
    env = {**os.environ, "DISPLAY": ""}
    subprocess.run([sys.executable, "-c", "import scripts.backtest_gui"], env=env,
                   check=True, capture_output=True, text=True)


@pytest.mark.parametrize("grid", [False, True], ids=["single", "grid"])
def test_fake_api_cli_end_to_end(monkeypatch, tmp_path, capsys, grid):
    prices = frame([3, 2, 1, 2, 3, 4, 3, 2, 1, 2, 3, 4])
    api = Mock()
    api.get_history.return_value = {"history": prices.to_dict("records")}
    api.get_financial_history.return_value = {"history": [dict(period="2022", eps=1)]}
    api.get_financials.side_effect = AssertionError("Current financials must never be used")
    monkeypatch.setattr(cli, "APIClient", Mock(return_value=api))
    options = ["--grid", "short_window=1,2", "--rank-by", "return_pct", "--grid-csv", str(tmp_path / "grid.csv")] if grid else []
    monkeypatch.setattr(sys, "argv", ["backtest_cli.py", "--symbol", "TEST", "--start-date", "2024-01-01",
                                     "--end-date", "2024-01-12", "--short-window", "2", "--long-window", "3",
                                     "--max-per", "20", "--initial-capital", "1000", "--execution", "close",
                                     "--trade-csv", str(tmp_path / "trades.csv"),
                                     "--metrics-csv", str(tmp_path / "metrics.csv"), *options])
    assert cli.main() == 0
    api.get_history.assert_called_once()
    api.get_financial_history.assert_called_once()
    api.get_financial_history.assert_called_once_with("TEST", limit=10, cancel_check=None)
    metrics = pd.read_csv(tmp_path / "metrics.csv")
    assert {"sharpe", "sortino", "benchmark_cagr"} <= set(metrics)
    # SMA2: buy333@3, sell333@2 => 667; buy222@3, cash1 + 222*4=889.
    # Grid winner SMA1: buy500@2, sell500@3 =>1500; buy750@2, mark4=>3000.
    assert metrics.final_asset.iloc[0] == pytest.approx(3000 if grid else 889)
    assert "signal_date" in pd.read_csv(tmp_path / "trades.csv")
    if grid:
        assert len(pd.read_csv(tmp_path / "grid.csv")) == 2
    output = capsys.readouterr().out
    assert "Portfolio Summary" in output and "benchmark_return_pct" in output
    print(output, end="")
