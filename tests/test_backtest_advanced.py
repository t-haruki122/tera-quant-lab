import pandas as pd
import pytest

from app.backtest.engine import Backtester, BacktestInputError
from app.backtest.portfolio import run_portfolio
from app.backtest.strategies import make_strategy
from app.backtest.walkforward import Fold, run_walkforward, split_folds


def prices(values, days=None, opening=None):
    return pd.DataFrame({
        "date": [f"2024-01-{day:02}" for day in (days or range(1, len(values) + 1))],
        "close": values, "open": values if opening is None else opening,
    })


def portfolio(frames, **options):
    return run_portfolio(frames, **{
        "strategy": "momentum", "params": {"period": 1}, "initial_capital": 100,
        "execution": "close", **options,
    })


def test_equal_and_equal_active_use_different_entry_budgets():
    frames = {"A": prices([10, 20, 40, 40]), "B": prices([20, 20, 20, 20])}
    equal = portfolio(frames)
    active = portfolio(frames, allocation="equal_active")
    assert [trade["shares"] for trade in equal.trades] == [2]
    assert [trade["shares"] for trade in active.trades] == [5]
    assert equal.equity_curve["equity"].tolist() == [100, 100, 140, 140]
    assert active.equity_curve["equity"].tolist() == [100, 100, 200, 200]
    assert equal.benchmark_metrics.final_asset == 250
    assert equal.metrics.benchmark_return_pct == active.metrics.benchmark_return_pct == 150


def test_rebalances_after_sell_with_integer_shares_fees_slippage_and_contribution():
    frames = {"A": prices([10, 20, 60, 60]), "B": prices([10, 20, 20, 20])}
    options = dict(initial_capital=1000, fee_rate=0.01, slippage_bps=100)
    held = portfolio(frames, **options)
    rebalanced = portfolio(frames, rebalance_every=2, **options)
    assert len(held.trades) == 2
    assert [(trade["symbol"], trade["side"]) for trade in rebalanced.trades] == [
        ("A", "BUY"), ("B", "BUY"), ("A", "SELL"), ("B", "BUY"),
    ]
    for trade in rebalanced.trades:
        assert type(trade["shares"]) is int and trade["shares"] > 0
        assert trade["fee"] == trade["shares"] * trade["price"] * 0.01
        assert trade["slippage"] > 0 and trade["cash_after"] >= 0
    assert (rebalanced.equity_curve["cash"] >= 0).all()
    assert sum(rebalanced.contributions.values()) == pytest.approx(rebalanced.metrics.final_asset - 1000)


def test_rebalance_threshold_and_signal_only_closed_trade_statistics():
    frames = {"A": prices([10, 20, 20.5, 19]), "B": prices([10, 20, 20, 20])}
    default = portfolio(frames, initial_capital=1000, rebalance_every=2)
    always = portfolio(frames, initial_capital=1000, rebalance_every=2, rebalance_threshold_pct=0)
    assert {trade["reason"] for trade in default.trades} == {"signal"}
    assert [(trade["side"], trade["reason"], trade["pnl"]) for trade in always.trades if trade["side"] == "SELL"] == [
        ("SELL", "rebalance", 0.5), ("SELL", "signal", -24),
    ]
    assert always.metrics.total_trades == 1
    assert always.metrics.win_rate == always.metrics.profit_factor == 0
    assert always.metrics.average_win is None and always.metrics.average_loss == -24
    assert always.metrics.final_asset > default.metrics.final_asset
    assert sum(always.contributions.values()) == pytest.approx(always.metrics.final_asset - 1000)


def test_next_open_waits_for_own_bar_and_marks_holiday_at_last_close():
    frames = {
        "A": prices([10, 20, 30, 50], [1, 2, 4, 6], [10, 20, None, 40]),
        "B": prices([10, 10, 20, 30], [1, 3, 5, 6]),
    }
    result = portfolio(frames, execution="next_open", initial_capital=1000, rebalance_every=2)
    curve = result.equity_curve
    assert curve["date"].tolist() == [f"2024-01-0{day}" for day in range(1, 7)]
    assert curve["holdings"].iloc[3]["A"] == curve["holdings"].iloc[4]["A"] == 480
    first = result.trades[0]
    assert (first["symbol"], first["signal_date"], first["date"], first["price"]) == ("A", "2024-01-02", "2024-01-04", 30)
    assert first["open_fallback"]
    for trade in result.trades:
        assert trade["date"] in set(frames[trade["symbol"]]["date"])
    assert "始値が欠損または無効" in result.warnings[-1]


@pytest.mark.parametrize(("frames", "options", "message"), [
    ({"A": prices([1])}, {}, "2〜10件"),
    ({"A": prices([1]), "a": prices([1])}, {}, "重複"),
    ({"A": prices([1]), "B": prices([])}, {}, "B の指定期間"),
    ({"A": prices([1]), "B": prices([0])}, {}, "B の終値"),
    ({"A": prices([1]), "B": prices([2])}, {"allocation": "other"}, "配分"),
    ({"A": prices([1]), "B": prices([2])}, {"rebalance_every": 0}, "リバランス"),
    ({"A": prices([1]), "B": prices([2])}, {"rebalance_threshold_pct": float("inf")}, "許容乖離率"),
])
def test_portfolio_validation(frames, options, message):
    with pytest.raises(BacktestInputError, match=message):
        portfolio(frames, **options)


def test_fold_boundaries():
    assert split_folds(11, 3, "rolling") == [Fold(0, 5, 7), Fold(2, 7, 9), Fold(4, 9, 11)]
    assert split_folds(11, 3, "anchored") == [Fold(0, 5, 7), Fold(0, 7, 9), Fold(0, 9, 11)]
    with pytest.raises(BacktestInputError, match="バーが不足"):
        split_folds(7, 3, "rolling")


def test_walkforward_no_lookahead_capital_and_warmup():
    frame = prices([5, 6, 7, 8, 10, 12, 15, 18, 20, 24, 30, 35, 40])
    options = dict(initial_capital=1000, strategy="momentum", grid={"period": [2, 3]},
                   execution="close", rank_by="return_pct", folds=2, context={"start_date": "2024-01-04"})
    result = run_walkforward("A", frame, **options)
    first, second = result.folds
    assert (first["train_start"], first["train_end"], first["test_start"], first["test_end"]) == (
        "2024-01-04", "2024-01-07", "2024-01-08", "2024-01-10",
    )
    standalone = Backtester(make_strategy("momentum", first["params"]), 1000, execution="close").run(
        "A", frame.iloc[:10], {"start_date": first["test_start"]},
    )
    assert first["oos_metrics"] == standalone.metrics
    assert result.trades[0]["date"] == first["test_start"]  # Indicators had their lookback immediately.
    assert second["oos_metrics"].initial_capital == first["oos_metrics"].final_asset
    assert result.metrics.final_asset == second["oos_metrics"].final_asset
    assert result.equity_curve["date"].tolist() == frame["date"].tolist()[7:]
    assert result.robustness["profitable_share"] == 1
    changed = frame.copy()
    changed.loc[7:, "close"] = [80, 12, 200, 52, 31, 900]
    alternative = run_walkforward("A", changed, **options)
    assert alternative.folds[0]["params"] == first["params"]
    assert alternative.folds[0]["is_metric"] == first["is_metric"]
    assert alternative.folds[0]["oos_metrics"].return_pct != first["oos_metrics"].return_pct


def test_walkforward_work_limit():
    with pytest.raises(BacktestInputError, match="積は 500 以下"):
        run_walkforward("A", prices([1]), initial_capital=100, strategy="momentum",
                        grid={"period": list(range(1, 101))}, folds=6)


def test_walkforward_reports_when_every_in_sample_score_is_missing():
    result = run_walkforward("A", prices([10] * 9), initial_capital=100, strategy="momentum",
                             grid={"period": [3, 1]}, folds=2, rank_by="sharpe")
    for index, fold in enumerate(result.folds, 1):
        assert fold["params"]["period"] == 3
        assert fold["is_metric"] is None
        assert fold["warning"] == f"分割 {index}: 学習期間で有効な指標が得られなかったため、先頭のパラメータを使用しました。"
