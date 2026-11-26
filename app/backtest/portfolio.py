"""Long-only portfolio simulation on the union of each symbol's trading dates."""

from dataclasses import dataclass, field
from math import floor
from typing import cast

import pandas as pd

from app.validation import currency_code, normalize_symbol

from .engine import Backtester, BacktestInputError
from .metrics import BacktestMetrics, calculate_metrics
from .pit import number
from .strategies import make_strategy

DEFAULT_REBALANCE_THRESHOLD_PCT = 5.0


def _currency_warnings(symbols, currencies):
    if currencies is None:
        return []
    groups: dict[str, list[str]] = {}
    unknown = []
    for symbol in symbols:
        code = currency_code(currencies.get(symbol))
        if code is None:
            unknown.append(symbol)
        else:
            groups.setdefault(code, []).append(symbol)
    if len(groups) > 1:
        description = " / ".join(f"{code}: {', '.join(members)}" for code, members in groups.items())
        raise BacktestInputError(f"通貨が異なる銘柄（{description}）は同じポートフォリオで検証できません")
    return [f"{', '.join(unknown)} の通貨を確認できなかったため、通貨が一致しているか確認せずに計算しました。"] if unknown else []


def validate_symbols(symbols: list[str]) -> list[str]:
    try:
        normalized = [normalize_symbol(symbol) for symbol in symbols]
    except (AttributeError, ValueError) as exc:
        raise BacktestInputError("銘柄は英数字または ^ . = - で20文字以内にしてください。") from exc
    if not 2 <= len(normalized) <= 10 or len(set(normalized)) != len(normalized):
        raise BacktestInputError("重複のない銘柄を2〜10件指定してください。")
    return normalized


@dataclass
class PortfolioResult:
    equity_curve: pd.DataFrame
    trades: list[dict]
    metrics: BacktestMetrics
    benchmark_metrics: BacktestMetrics
    contributions: dict[str, float]
    warnings: list[str]


@dataclass
class Position:
    shares: int = 0
    cost: float = 0.0
    net_cash: float = 0.0
    close: float = 0.0
    active: bool = False
    pending: int = 0
    signal_date: str | None = None


@dataclass
class PortfolioBook:
    cash: float
    positions: dict[str, Position]
    trades: list[dict] = field(default_factory=list)

    def fill(self, symbol, day, signal_date, raw, delta, engine, fallback, budget, reason):
        position = self.positions[symbol]
        selling = delta < 0
        price = raw * (1 + engine.slippage_bps / 10000 * (-1 if selling else 1))
        quantity = -delta if selling else min(delta, floor(min(budget, self.cash) / (price * (1 + engine.fee_rate))))
        pnl = None
        while quantity > 0:
            notional = quantity * price
            fee = notional * engine.fee_rate
            if selling:
                basis = position.cost if quantity == position.shares else position.cost * quantity / position.shares
                position.cost -= basis
                movement = notional - fee
                pnl = movement - basis
                break
            movement = -(notional + fee)
            if -movement <= min(budget, self.cash):
                position.cost -= movement
                break
            quantity -= 1
        else:
            return
        position.shares += -quantity if selling else quantity
        self.cash += movement
        position.net_cash += movement
        self.trades.append(dict(
            symbol=symbol, date=day, signal_date=signal_date, side="SELL" if selling else "BUY",
            price=price, shares=quantity, fee=fee, pnl=pnl, cash_after=self.cash,
            slippage=abs(price - raw) * quantity, open_fallback=fallback, reason=reason,
        ))


def _prepare(engine, frames, contexts, start_date, end_date, cancel_check):
    bars: dict[str, dict[str, dict]] = {}
    warnings = []
    missing_eps = 0
    for symbol, frame in frames.items():
        if cancel_check and cancel_check():
            raise RuntimeError("Cancelled")
        if not {"date", "close"} <= set(frame):
            raise BacktestInputError(f"{symbol} の株価履歴の形式が正しくありません。")
        dates = cast(pd.Series, frame["date"])
        if dates.isna().any() or dates.duplicated().any() or not dates.is_monotonic_increasing:
            raise BacktestInputError(f"{symbol} の株価の日付は重複のない昇順で指定してください。")
        if any(number(value) is None or float(value) <= 0 for value in frame["close"]):
            raise BacktestInputError(f"{symbol} の終値は有限の正の数値で指定してください。")
        window = frame if end_date is None else frame.loc[dates <= end_date]
        context = contexts.get(symbol, {})
        prepared = engine.strategy.prepare(window.reset_index(drop=True), context)
        if start_date is not None:
            prepared = prepared.loc[prepared["date"] >= start_date]
        if prepared.empty:
            raise BacktestInputError(f"{symbol} の指定期間に株価データがありません。")
        missing = int(prepared["eps"].isna().to_numpy().sum()) if "eps" in prepared else 0
        missing_eps += missing
        if missing:
            warnings.append(f"{symbol} の過去のEPSが {len(prepared)} バー中 {missing} バーで取得できず、PER条件により買いを見送りました。")
        for row in prepared.to_dict("records"):
            bars.setdefault(row["date"], {})[symbol] = row
    return bars, warnings, missing_eps


def _simulate(engine, bars, symbols, allocation, rebalance_every, rebalance_threshold_pct, benchmark, cancel_check):
    book = PortfolioBook(float(engine.initial_capital), {symbol: Position() for symbol in symbols})
    rows = []
    rebalance_due = set()
    for index, (day, daily) in enumerate(sorted(bars.items())):
        if cancel_check and cancel_check():
            raise RuntimeError("Cancelled")
        if rebalance_every and index and index % rebalance_every == 0:
            rebalance_due.update(symbols)
        quotes = {}
        orders = {}
        entries = set()
        for symbol, row in daily.items():
            position = book.positions[symbol]
            signal = int(position.signal_date is None) if benchmark else int(row["signal"])
            close = float(row["close"])
            opening = number(row.get("open")) if engine.execution == "next_open" else close
            fallback = opening is None or opening <= 0
            quotes[symbol] = (close if opening is None or opening <= 0 else opening, fallback)
            if engine.execution == "next_open":
                orders[symbol] = (position.pending, position.signal_date)
            else:
                orders[symbol] = (signal, day)
            position.pending, position.signal_date = signal, day
            order, _ = orders[symbol]
            if order:
                position.active = order == 1
            if order == 1 and not position.shares:
                entries.add(symbol)

        equity = book.cash + sum(
            position.shares * (quotes[symbol][0] if symbol in quotes else position.close)
            for symbol, position in book.positions.items()
        )
        divisor = len(symbols) if allocation == "equal" else sum(p.active for p in book.positions.values())
        budget = (engine.initial_capital if benchmark else equity) / max(1, divisor)
        adjustments = {}
        for symbol in daily:
            position = book.positions[symbol]
            raw, _ = quotes[symbol]
            if not position.active:
                adjustments[symbol] = (-position.shares, "signal")
            elif symbol in entries:
                cost = raw * (1 + engine.slippage_bps / 10000) * (1 + engine.fee_rate)
                adjustments[symbol] = (floor(budget / cost), "signal")
            elif symbol in rebalance_due and (
                rebalance_threshold_pct == 0 or abs(position.shares * raw - budget) > budget * (rebalance_threshold_pct / 100)
            ):
                adjustments[symbol] = (floor(budget / raw) - position.shares, "rebalance")
        rebalance_due.difference_update(daily)

        for symbol, (delta, reason) in adjustments.items():
            if delta < 0:
                raw, fallback = quotes[symbol]
                signal_date = orders[symbol][1] if reason == "signal" else None
                book.fill(symbol, day, signal_date, raw, delta, engine, fallback, book.cash, reason)
        buys = {}
        for symbol, (delta, _) in adjustments.items():
            if delta > 0:
                price = quotes[symbol][0] * (1 + engine.slippage_bps / 10000)
                notional = delta * price
                buys[symbol] = notional + notional * engine.fee_rate
        # Proportional budgets keep simultaneous orders independent of symbol priority.
        available = book.cash
        total_cost = sum(buys.values())
        for symbol, cost in buys.items():
            raw, fallback = quotes[symbol]
            maximum = book.cash if total_cost <= available else available * (cost / total_cost)
            delta, reason = adjustments[symbol]
            signal_date = orders[symbol][1] if reason == "signal" else None
            book.fill(symbol, day, signal_date, raw, delta, engine, fallback, maximum, reason)
        for symbol, row in daily.items():
            book.positions[symbol].close = float(row["close"])
        holdings = {symbol: p.shares * p.close for symbol, p in book.positions.items()}
        rows.append(dict(date=day, cash=book.cash, equity=book.cash + sum(holdings.values()),
                         shares=sum(p.shares for p in book.positions.values()), holdings=holdings))
    return pd.DataFrame(rows), book


def run_portfolio(
    frames: dict[str, pd.DataFrame], *, initial_capital: float, strategy: str = "sma_cross", params: dict | None = None,
    allocation: str = "equal", rebalance_every: int | None = None, contexts: dict | None = None,
    rebalance_threshold_pct: float = DEFAULT_REBALANCE_THRESHOLD_PCT, currencies: dict[str, str | None] | None = None,
    start_date: str | None = None, end_date: str | None = None, cancel_check=None, **engine_options,
) -> PortfolioResult:
    symbols = validate_symbols(list(frames))
    if allocation not in {"equal", "equal_active"}:
        raise BacktestInputError("配分は均等または買いシグナルが有効な銘柄間の均等を指定してください。")
    if rebalance_every is not None and (type(rebalance_every) is not int or rebalance_every < 1):
        raise BacktestInputError("リバランス間隔は1以上の整数で指定してください。")
    threshold = number(rebalance_threshold_pct)
    if threshold is None or threshold < 0:
        raise BacktestInputError("リバランスの許容乖離率は0以上の有限の数値を指定してください。")
    if start_date and end_date and start_date > end_date:
        raise BacktestInputError("開始日は終了日以前にしてください。")
    try:
        engine = Backtester(make_strategy(strategy, params), initial_capital=initial_capital, **engine_options)
    except ValueError as exc:
        raise BacktestInputError(str(exc)) from exc
    currency_warnings = _currency_warnings(symbols, currencies)
    frames = dict(zip(symbols, frames.values(), strict=True))
    bars, warnings, missing_eps = _prepare(engine, frames, contexts or {}, start_date, end_date, cancel_check)
    warnings.extend(currency_warnings)
    curve, book = _simulate(engine, bars, symbols, allocation, rebalance_every, threshold, False, cancel_check)
    benchmark, bench_book = _simulate(engine, bars, symbols, "equal", None, 0, True, cancel_check)
    name = ", ".join(symbols)
    signal_trades = [trade for trade in book.trades if trade["reason"] == "signal"]
    metrics = calculate_metrics(name, curve, signal_trades, engine.initial_capital, engine.interval, engine.risk_free)
    bench_metrics = calculate_metrics(name, benchmark, bench_book.trades, engine.initial_capital, engine.interval, engine.risk_free)
    metrics.benchmark_return_pct = bench_metrics.return_pct
    metrics.benchmark_cagr = bench_metrics.cagr
    metrics.benchmark_max_drawdown = bench_metrics.max_drawdown
    metrics.open_fallbacks = sum(t["open_fallback"] for t in book.trades)
    metrics.missing_eps_bars = missing_eps
    bench_metrics.open_fallbacks = sum(t["open_fallback"] for t in bench_book.trades)
    metrics.benchmark_open_fallbacks = bench_metrics.open_fallbacks
    if metrics.open_fallbacks or bench_metrics.open_fallbacks:
        warnings.append(f"始値が欠損または無効のため、戦略で {metrics.open_fallbacks} 件、買い持ちで {bench_metrics.open_fallbacks} 件を終値で約定しました。")
    contributions = {s: p.net_cash + p.shares * p.close for s, p in book.positions.items()}
    curve["benchmark_equity"] = benchmark["equity"]
    return PortfolioResult(curve, book.trades, metrics, bench_metrics, contributions, warnings)
