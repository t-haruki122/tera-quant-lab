"""Reusable, I/O-free long-only integer-share backtest engine."""

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from .metrics import ANNUALISATION, BacktestMetrics, calculate_metrics
from .pit import number
from .strategies import Strategy


class BacktestInputError(ValueError):
    """バックテストの設定や価格データに関する入力エラー。"""


@dataclass
class BacktestResult:
    symbol: str
    equity_curve: pd.DataFrame
    trades: list[dict[str, Any]]
    metrics: BacktestMetrics
    warnings: list[str] = field(default_factory=list)
    benchmark_curve: pd.DataFrame = field(default_factory=pd.DataFrame)
    benchmark_metrics: BacktestMetrics | None = None


def history_to_df(payload):
    rows = payload.get("history", [])
    if not rows:
        return pd.DataFrame(columns=["date", "open", "close"])
    df = pd.DataFrame(rows)
    if "date" not in df or "close" not in df:
        raise BacktestInputError("株価履歴の形式が正しくありません")
    df["date"] = pd.to_datetime(df["date"], errors="coerce", utc=True)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    if "open" not in df:
        df["open"] = float("nan")
    return df


class Backtester:
    def __init__(self, strategy: Strategy, initial_capital: float, fee_rate=0.0,
                 execution="next_open", slippage_bps=0.0, interval="1d", risk_free=0.0):
        for key, value in dict(initial_capital=initial_capital, fee_rate=fee_rate,
                               slippage_bps=slippage_bps, risk_free=risk_free).items():
            if number(value) is None:
                raise BacktestInputError(f"{key} は有限の数値を指定してください")
        if initial_capital <= 0 or not 0 <= fee_rate < 1 or not 0 <= slippage_bps < 10000 or risk_free <= -1:
            raise BacktestInputError("条件を満たす値を指定してください（initial_capital > 0、0 <= fee_rate < 1、0 <= slippage_bps < 10000、risk_free > -1）")
        if execution not in ("next_open", "close") or interval not in ANNUALISATION:
            raise BacktestInputError("約定タイミングまたはデータ間隔が正しくありません")
        self.strategy, self.initial_capital, self.fee_rate = strategy, initial_capital, fee_rate
        self.execution, self.slippage_bps, self.interval, self.risk_free = execution, slippage_bps, interval, risk_free

    def run(self, symbol, price_df, context=None, progress_callback=None, cancel_check=None):
        if cancel_check and cancel_check():
            raise RuntimeError("Cancelled")
        if price_df.empty:
            raise BacktestInputError(f"{symbol} の株価データがありません")
        if any(number(value) is None or float(value) <= 0 for value in price_df["close"]):
            raise BacktestInputError("終値は有限の正の数値である必要があります")
        if price_df["date"].duplicated().any() or not price_df["date"].is_monotonic_increasing:
            raise BacktestInputError("株価データの日付は重複がなく、昇順である必要があります")
        context = context or {}
        df = self.strategy.prepare(price_df.reset_index(drop=True), context)
        if (start_date := context.get("start_date")) is not None:
            df = df.loc[df["date"] >= start_date].reset_index(drop=True)
            if df.empty:
                raise BacktestInputError(f"{symbol} の指定期間に株価データがありません")
        curve, trades, fallbacks = self._simulate(symbol, df, progress_callback, cancel_check)
        benchmark = df.copy()
        benchmark["signal"] = 0
        benchmark.loc[benchmark.index[0], "signal"] = 1
        bench_curve, bench_trades, bench_fallbacks = self._simulate(symbol, benchmark, None, cancel_check)
        metrics = self._calculate_metrics(symbol, curve, trades)
        bench_metrics = self._calculate_metrics(symbol, bench_curve, bench_trades)
        metrics.open_fallbacks = fallbacks
        bench_metrics.open_fallbacks = bench_fallbacks
        metrics.benchmark_open_fallbacks = bench_fallbacks
        metrics.benchmark_return_pct = bench_metrics.return_pct
        metrics.benchmark_cagr = bench_metrics.cagr
        metrics.benchmark_max_drawdown = bench_metrics.max_drawdown
        metrics.missing_eps_bars = int(df["eps"].isna().to_numpy().sum()) if "eps" in df else 0
        warnings = []
        if metrics.missing_eps_bars:
            warnings.append(f"Historical EPS unavailable on {metrics.missing_eps_bars}/{len(df)} bars; PER blocks entries on those bars.")
        if fallbacks or bench_fallbacks:
            warnings.append(f"Missing/invalid open: close fallback on {fallbacks} strategy fills, {bench_fallbacks} benchmark fills.")
        return BacktestResult(symbol, curve, trades, metrics, warnings, bench_curve, bench_metrics)

    def _simulate(self, symbol, df, progress_callback, cancel_check):
        cash, shares, position_cost = float(self.initial_capital), 0, 0.0
        trades, rows, fallbacks = [], [], 0
        pending = 0
        for idx, row in enumerate(df.to_dict("records")):
            if cancel_check and cancel_check():
                raise RuntimeError("Cancelled")
            signal, close = int(row["signal"]), float(row["close"])
            order = pending if self.execution == "next_open" else signal
            pending = signal
            side = None
            fill_price = close
            if (order == 1 and shares == 0) or (order == -1 and shares > 0):
                opening_price = number(row.get("open")) if self.execution == "next_open" else close
                if opening_price is None or opening_price <= 0:
                    raw_price, fallback = close, True
                else:
                    raw_price, fallback = opening_price, False
                price = raw_price * (1 + self.slippage_bps / 10000 * (1 if order == 1 else -1))
                quantity = int(cash / (price * (1 + self.fee_rate))) if order == 1 else shares
                if quantity > 0:
                    fill_price = price
                    fallbacks += int(fallback)
                    fee = quantity * price * self.fee_rate
                    pnl = None
                    if order == 1:
                        position_cost = quantity * price + fee
                        cash -= position_cost
                        shares, side = quantity, "BUY"
                    else:
                        proceeds = quantity * price - fee
                        pnl = proceeds - position_cost
                        cash += proceeds
                        shares, side, position_cost = 0, "SELL", 0.0
                    # Keep full precision for accounting, trade statistics and CSV output.
                    trades.append(dict(symbol=symbol, date=row["date"], side=side, price=price,
                                       shares=quantity, fee=fee, pnl=pnl, cash_after=cash,
                                       signal_date=df.iloc[idx - 1]["date"] if self.execution == "next_open" else row["date"],
                                       slippage=abs(price - raw_price) * quantity, open_fallback=fallback))
            equity = cash + shares * close
            rows.append(dict(symbol=symbol, date=row["date"], close=close, shares=shares, cash=cash,
                             holding_value=shares * close, equity=equity, signal=signal, trade_side=side))
            if progress_callback:
                progress_callback(idx + 1, len(df), dict(symbol=symbol, date=row["date"], signal=signal,
                                                       trade_side=side, cash=cash, equity=equity, price=fill_price))
        return pd.DataFrame(rows), trades, fallbacks

    def _calculate_metrics(self, symbol, equity_df, trades):
        return calculate_metrics(symbol, equity_df, trades, self.initial_capital, self.interval, self.risk_free)
