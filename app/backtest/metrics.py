"""Pure close-to-close portfolio statistics (percentage fields use 0..100 units)."""

from dataclasses import dataclass
from math import inf, sqrt

import numpy as np

ANNUALISATION = {"1d": 252, "1wk": 52, "1mo": 12}


@dataclass
class BacktestMetrics:
    symbol: str
    initial_capital: float
    final_asset: float
    return_pct: float
    cagr: float
    annualised_volatility: float
    sharpe: float | None
    sortino: float | None
    total_trades: int
    win_rate: float
    profit_factor: float | None
    average_win: float | None
    average_loss: float | None
    exposure: float
    max_drawdown: float
    max_drawdown_duration: int
    open_fallbacks: int = 0
    missing_eps_bars: int = 0
    benchmark_return_pct: float = 0.0
    benchmark_cagr: float = 0.0
    benchmark_max_drawdown: float = 0.0
    benchmark_open_fallbacks: int = 0


def calculate_metrics(symbol, equity_df, trades, initial_capital, interval="1d", risk_free=0.0):
    factor = ANNUALISATION[interval]
    values = np.asarray([initial_capital, *equity_df["equity"]], dtype=float)
    returns = values[1:] / values[:-1] - 1
    excess = returns - ((1 + risk_free) ** (1 / factor) - 1)
    volatility = float(returns.std(ddof=0))
    downside = sqrt(float(np.minimum(excess, 0).dot(np.minimum(excess, 0))) / len(excess))
    peaks = np.maximum.accumulate(values)
    drawdown = 1 - values / peaks
    duration = longest = 0
    for value in drawdown[1:]:
        duration = duration + 1 if value > 0 else 0
        longest = max(longest, duration)
    pnls = [float(t["pnl"]) for t in trades if t["side"] == "SELL" and t["pnl"] is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    profit_factor = sum(wins) / -sum(losses) if losses else (inf if wins else None)
    ratio = values[-1] / initial_capital
    return BacktestMetrics(
        symbol=symbol, initial_capital=initial_capital, final_asset=float(values[-1]),
        return_pct=(ratio - 1) * 100, cagr=(ratio ** (factor / len(returns)) - 1) * 100,
        annualised_volatility=volatility * sqrt(factor) * 100,
        sharpe=float(excess.mean()) / volatility * sqrt(factor) if volatility > 1e-15 else None,
        sortino=float(excess.mean()) / downside * sqrt(factor) if downside > 1e-15 else None,
        total_trades=len(pnls), win_rate=len(wins) / len(pnls) * 100 if pnls else 0.0,
        profit_factor=profit_factor,
        average_win=sum(wins) / len(wins) if wins else None,
        average_loss=sum(losses) / len(losses) if losses else None,
        exposure=float((equity_df["shares"] > 0).mean()) * 100,
        max_drawdown=float(drawdown.max()) * 100, max_drawdown_duration=longest,
    )
