"""py_stock_data_api を使ったバックテストCLI。

例:
python scripts/backtest_cli.py --symbol AAPL --start-date 2023-01-01 --end-date 2024-12-31
python scripts/backtest_cli.py --user-id 1 --watchlist-id 2 --start-date 2023-01-01 --end-date 2024-12-31 --save-chart
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import getpass
import math
import os
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import quote

import pandas as pd
import requests

# Direct execution from scripts/ also works without installing the project.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.backtest import (  # noqa: E402
    REGISTRY,
    Backtester,
    BacktestMetrics,
    BacktestResult,
    grid_params,
    history_to_df,
    make_strategy,
    parse_assignments,
    run_grid,
    validate_params,
)
from app.backtest import (  # noqa: E402
    SmaCrossPerStrategy as SmaCrossPerStrategy,
)
from app.backtest import (  # noqa: E402
    Strategy as Strategy,
)
from app.backtest.grid import RANK_METRICS  # noqa: E402


class APIClient:
    def __init__(
        self,
        base_url: str,
        timeout: float = 10.0,
        max_retries: int = 3,
        retry_wait: float = 1.0,
        token: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_wait = retry_wait
        self.session = requests.Session()
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    def login(self, username: str, password: str) -> dict[str, Any]:
        payload = self._request(
            "POST", "/user/login", json={"username": username, "password": password}
        )
        self.session.headers["Authorization"] = f"Bearer {payload['access_token']}"
        return payload

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        cancel_check: Callable[[], bool] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            retry_after = None
            if cancel_check is not None and cancel_check():
                raise RuntimeError("Cancelled")
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    params=params,
                    timeout=self.timeout,
                    json=json,
                )
            except (requests.Timeout, requests.ConnectionError, RuntimeError) as exc:
                last_error = exc
                if str(exc) == "Cancelled":
                    raise
            else:
                if response.status_code == 404:
                    raise RuntimeError(f"404 Not Found: {url}")
                if response.status_code < 400:
                    return response.json()

                detail = response.text[:200]
                if response.status_code in (401, 403):
                    try:
                        payload = response.json()
                    except ValueError:
                        pass
                    else:
                        if isinstance(payload, dict) and payload.get("detail"):
                            detail = payload["detail"]
                last_error = RuntimeError(f"HTTP {response.status_code}: {url} - {detail}")
                if response.status_code == 429:
                    try:
                        seconds = float(getattr(response, "headers", {}).get("Retry-After", ""))
                    except (ValueError, TypeError):
                        pass
                    else:
                        if math.isfinite(seconds) and seconds >= 0:
                            retry_after = min(seconds, 30.0)
                if 400 <= response.status_code < 500 and response.status_code != 429:
                    raise last_error

            if attempt >= self.max_retries:
                break
            sleep_sec = retry_after if retry_after is not None else self.retry_wait * attempt
            # Sleep in short chunks so cancellation can interrupt retry waits.
            waited = 0.0
            while waited < sleep_sec:
                if cancel_check is not None and cancel_check():
                    raise RuntimeError("Cancelled")
                chunk = min(0.1, sleep_sec - waited)
                time.sleep(chunk)
                waited += chunk

        raise RuntimeError(f"API request failed after retries: {url} ({last_error})")

    def get_history(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        interval: str,
        cancel_check: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/stock/{quote(symbol, safe="")}/history",
            params={
                "start_date": start_date,
                "end_date": end_date,
                "interval": interval,
            },
            cancel_check=cancel_check,
        )

    def get_financials(
        self,
        symbol: str,
        cancel_check: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        return self._request("GET", f"/stock/{quote(symbol, safe="")}/financials", cancel_check=cancel_check)

    def get_financial_history(
        self,
        symbol: str,
        limit: int = 6,
        cancel_check: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/stock/{quote(symbol, safe="")}/financials/history",
            params={"limit": limit},
            cancel_check=cancel_check,
        )

    def get_watchlist_symbols(
        self,
        user_id: int,
        watchlist_id: int,
        cancel_check: Callable[[], bool] | None = None,
    ) -> list[str]:
        payload = self._request("GET", f"/user/{user_id}/lists/{watchlist_id}", cancel_check=cancel_check)
        items = payload.get("items", [])
        symbols = [str(item.get("symbol", "")).upper() for item in items if item.get("symbol")]
        return sorted(set(symbols))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="py_stock_data_api バックテストCLI")

    target_group = parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument("--symbol", type=str, help="単一銘柄シンボル (例: AAPL)")
    target_group.add_argument("--watchlist-id", type=int, help="ウォッチリストID")

    parser.add_argument("--user-id", type=int, help="ウォッチリスト取得用のユーザーID (--username 指定時は省略可)")
    auth_group = parser.add_mutually_exclusive_group()
    auth_group.add_argument("--username", help="ログインユーザー名 (パスワード: TQL_PASSWORD または非表示入力)")
    auth_group.add_argument("--token", help="Bearerトークン (未指定時: TQL_TOKEN)")

    parser.add_argument("--start-date", required=True, help="開始日 YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="終了日 YYYY-MM-DD")
    parser.add_argument("--interval", default="1d", choices=["1d", "1wk", "1mo"], help="データ間隔")

    parser.add_argument("--initial-capital", type=float, default=1_000_000.0, help="初期資金")
    parser.add_argument("--fee-rate", type=float, default=0.0, help="手数料率（0.001 = 0.1%%）")

    parser.add_argument("--short-window", type=int, default=20, help="短期SMA期間")
    parser.add_argument("--long-window", type=int, default=60, help="長期SMA期間")
    parser.add_argument("--max-per", type=float, default=None, help="PER上限フィルター")

    parser.add_argument("--strategy", choices=list(REGISTRY), default="sma_cross", help="戦略名")
    parser.add_argument("--param", action="append", default=[], help="戦略パラメータ key=value (繰返し可)")
    parser.add_argument("--execution", choices=["next_open", "close"], default="next_open")
    parser.add_argument("--eps-lag-days", type=int, default=90)
    parser.add_argument("--risk-free", type=float, default=0.0, help="年率無リスク金利 (0.02 = 2%%)")
    parser.add_argument("--slippage-bps", type=float, default=0.0)
    parser.add_argument("--metrics-csv", default="metrics.csv")
    parser.add_argument("--grid", action="append", default=[], help="key=v1,v2,... (最大500組)")
    parser.add_argument("--rank-by", choices=sorted(RANK_METRICS), default="sharpe")
    parser.add_argument("--grid-csv")
    parser.add_argument("--fetch-financial-history", action="store_true", help="財務履歴を取得して表示")

    parser.add_argument("--base-url", default="http://localhost:8000", help="APIベースURL")
    parser.add_argument("--timeout", type=float, default=10.0, help="APIタイムアウト秒")
    parser.add_argument("--max-retries", type=int, default=3, help="API再試行回数")

    parser.add_argument("--trade-csv", default="trade_history.csv", help="取引履歴CSV出力先")
    parser.add_argument("--save-chart", action="store_true", help="チャート画像を保存")
    parser.add_argument("--chart-file", default="result_chart.png", help="単一銘柄のチャート出力先")
    parser.add_argument("--show-chart", action="store_true", help="チャートを画面表示")

    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    try:
        start = datetime.strptime(args.start_date, "%Y-%m-%d")
        end = datetime.strptime(args.end_date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("start-date/end-date は YYYY-MM-DD 形式で指定してください") from exc

    if start > end:
        raise ValueError("start-date は end-date 以前にしてください")

    if (
        getattr(args, "watchlist_id", None) is not None
        and getattr(args, "user_id", None) is None
        and not getattr(args, "username", None)
    ):
        raise ValueError("watchlist-id 指定時は --user-id が必要です（--username 指定時は省略可）")

    if args.initial_capital <= 0:
        raise ValueError("initial-capital は正の値を指定してください")

    if not math.isfinite(args.fee_rate) or args.fee_rate < 0:
        raise ValueError("fee-rate は 0 以上を指定してください")

    name, params, _ = strategy_options(args)
    Backtester(make_strategy(name, params), **engine_options(args, args.initial_capital))
    lag = getattr(args, "eps_lag_days", 90)
    if not isinstance(lag, int) or lag < 0:
        raise ValueError("eps-lag-days must be a nonnegative integer")


def metrics_text(metrics: BacktestMetrics) -> str:
    return "\n".join(f"{key}: {value:.6g}" if isinstance(value, float) else f"{key}: {value}"
                     for key, value in dataclasses.asdict(metrics).items())


def print_metrics(metrics: BacktestMetrics) -> None:
    print("=" * 70)
    print(metrics_text(metrics))


def save_metrics_csv(results, output_file):
    path = Path(output_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([dataclasses.asdict(r.metrics) for r in results]).to_csv(path, index=False)
    print(f"Metrics saved: {path}")


def save_trades_csv(trades: list[dict[str, Any]], output_file: str) -> None:
    fields = ["symbol", "date", "signal_date", "side", "price", "shares", "fee", "slippage", "open_fallback", "pnl", "cash_after"]
    out_path = Path(output_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for trade in trades:
            writer.writerow(trade)

    print(f"Trade history saved: {out_path}")


def plot_result(
    result: BacktestResult,
    output_file: str,
    show_chart: bool,
) -> None:
    import matplotlib.pyplot as plt

    fig = create_result_figure(result)
    out_path = Path(output_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    print(f"Chart saved: {out_path}")

    if show_chart:
        plt.show()
    else:
        plt.close(fig)


def create_result_figure(result: BacktestResult):
    import matplotlib.pyplot as plt

    eq = result.equity_curve.copy()
    eq["date"] = pd.to_datetime(eq["date"])

    fills = pd.DataFrame(result.trades, columns=["date", "side", "price"])
    fills["date"] = pd.to_datetime(fills["date"])
    buy_points = fills[fills["side"] == "BUY"]
    sell_points = fills[fills["side"] == "SELL"]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)

    ax1.plot(eq["date"], eq["close"], label="Close", color="steelblue", linewidth=1.2)
    ax1.scatter(buy_points["date"], buy_points["price"], marker="^", color="green", label="Buy", s=50)
    ax1.scatter(sell_points["date"], sell_points["price"], marker="v", color="red", label="Sell", s=50)
    ax1.set_title(f"{result.symbol} Price + Fills")
    ax1.set_ylabel("Price")
    ax1.grid(True, alpha=0.3)
    ax1.legend()

    ax2.plot(eq["date"], eq["equity"], label="Equity", color="darkorange", linewidth=1.2)
    ax2.set_title("Equity Curve")
    ax2.set_ylabel("Asset")
    ax2.grid(True, alpha=0.3)
    ax2.legend()

    plt.tight_layout()
    return fig


def strategy_options(args):
    name = getattr(args, "strategy", "sma_cross")
    params = {}
    if name == "sma_cross":
        params.update(short_window=args.short_window, long_window=args.long_window)
    if args.max_per is not None:
        params["max_per"] = args.max_per
    params.update(parse_assignments(getattr(args, "param", [])))
    params = validate_params(name, params)
    grid = {key: value.split(",") for key, value in parse_assignments(getattr(args, "grid", [])).items()}
    grid_params(name, params, grid)
    return name, params, grid


class EngineOptions(TypedDict):
    initial_capital: float
    fee_rate: float
    execution: str
    slippage_bps: float
    interval: str
    risk_free: float


def engine_options(args: argparse.Namespace, initial_capital: float) -> EngineOptions:
    return EngineOptions(
        initial_capital=initial_capital,
        fee_rate=args.fee_rate,
        execution=getattr(args, "execution", "next_open"),
        slippage_bps=getattr(args, "slippage_bps", 0.0),
        interval=args.interval,
        risk_free=getattr(args, "risk_free", 0.0),
    )


def fetch_context(client, symbol, args, params, grid, cancel_check=None):
    context = {"eps_lag_days": getattr(args, "eps_lag_days", 90)}
    if params.get("max_per") is not None or "max_per" in grid or args.fetch_financial_history:
        context["financial_history"] = client.get_financial_history(symbol, limit=10, cancel_check=cancel_check)
        if args.fetch_financial_history:
            print(f"Financial history fetched for {symbol}: {len(context['financial_history'].get('history', []))} rows")
    return context


def run_symbol_grid(client, symbol, args, initial_capital):
    name, params, grid = strategy_options(args)
    prices = history_to_df(client.get_history(symbol, args.start_date, args.end_date, args.interval))
    context = fetch_context(client, symbol, args, params, grid)
    return run_grid(symbol, prices, name, params, grid, args.rank_by, context, **engine_options(args, initial_capital))


def grid_rows(ranked):
    return [dict(rank=i, **{f"param_{k}": v for k, v in item.params.items()},
                 **dataclasses.asdict(item.result.metrics)) for i, item in enumerate(ranked, 1)]


def run_single_symbol(
    client: APIClient,
    symbol: str,
    args: argparse.Namespace,
    initial_capital: float,
    cancel_event: threading.Event | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
) -> BacktestResult:
    cancel_check = cancel_event.is_set if cancel_event is not None else None

    def emit_progress(progress: float, message: str) -> None:
        if progress_callback is not None:
            progress_callback(progress, message)

    emit_progress(5.0, f"{symbol}: price data fetching")
    history_payload = client.get_history(
        symbol,
        args.start_date,
        args.end_date,
        args.interval,
        cancel_check=cancel_check,
    )
    history_df = history_to_df(history_payload)
    emit_progress(20.0, f"{symbol}: price data ready ({len(history_df)} rows)")

    strategy_name, params, grid = strategy_options(args)
    context = fetch_context(client, symbol, args, params, grid, cancel_check)
    emit_progress(30.0, f"{symbol}: strategy ready")
    backtester = Backtester(make_strategy(strategy_name, params), **engine_options(args, initial_capital))

    def on_backtest_progress(done: int, total: int, state: dict[str, Any]) -> None:
        ratio = (done / total) if total > 0 else 1.0
        overall = 30.0 + ratio * 65.0
        message = f"{symbol}: {state['date']} processed ({done}/{total})"
        if state.get("trade_side") is not None:
            message += (
                f" | {state['trade_side']} @ {state['price']:.2f}"
                f" | cash {state['cash']:.2f}"
            )
        emit_progress(overall, message)

    result = backtester.run(
        symbol=symbol,
        price_df=history_df,
        context=context,
        progress_callback=on_backtest_progress,
        cancel_check=cancel_check,
    )
    emit_progress(100.0, f"{symbol}: backtest completed")
    return result


def resolve_symbols(client: APIClient, args: argparse.Namespace) -> list[str]:
    if args.symbol:
        return [args.symbol.upper()]

    # Authentication is only needed for watchlists. Never prompt from APIClient/GUI.
    user_id = getattr(args, "user_id", None)
    if getattr(args, "username", None):
        password = os.environ.get("TQL_PASSWORD")
        if password is None:
            password = getpass.getpass("Password: ")
        user = client.login(args.username, password)
        if user_id is not None and user_id != user["id"]:
            raise ValueError(f"--user-id {user_id} はログインユーザーのID {user['id']} と一致しません")
        user_id = user["id"]
    else:
        token = getattr(args, "token", None) or os.environ.get("TQL_TOKEN")
        if not token:
            raise ValueError("ウォッチリストには --username または --token / TQL_TOKEN が必要です")
        client.session.headers["Authorization"] = f"Bearer {token}"

    if user_id is None:
        raise ValueError("watchlist-id 指定時は --user-id が必要です（--username 指定時は省略可）")
    symbols = client.get_watchlist_symbols(user_id, args.watchlist_id)
    if not symbols:
        raise ValueError("ウォッチリストに銘柄が存在しません")
    return symbols


def main() -> int:
    args = parse_args()

    try:
        validate_args(args)
    except ValueError as exc:
        print(f"Argument error: {exc}", file=sys.stderr)
        return 1

    client = APIClient(
        base_url=args.base_url,
        timeout=args.timeout,
        max_retries=args.max_retries,
        retry_wait=1.0,
    )

    try:
        symbols = resolve_symbols(client, args)
    except Exception as exc:
        print(f"Failed to resolve symbols: {exc}", file=sys.stderr)
        return 1

    capital_per_symbol = args.initial_capital / len(symbols)

    all_results: list[BacktestResult] = []
    all_trades: list[dict[str, Any]] = []
    all_grid_rows = []

    for symbol in symbols:
        try:
            if args.grid:
                ranked = run_symbol_grid(client, symbol, args, capital_per_symbol)
                rows = grid_rows(ranked)
                all_grid_rows.extend(rows)
                print(f"Grid ranking: {symbol} by {args.rank_by}")
                print(pd.DataFrame(rows).to_string(index=False))
                for item in ranked:
                    for warning in item.result.warnings:
                        print(f"WARNING: {warning}")
                result = ranked[0].result
            else:
                result = run_single_symbol(client, symbol, args, initial_capital=capital_per_symbol)
                for warning in result.warnings:
                    print(f"WARNING: {warning}")
            all_results.append(result)
            all_trades.extend(result.trades)
            print_metrics(result.metrics)
        except Exception as exc:
            print(f"Backtest failed for {symbol}: {exc}", file=sys.stderr)

    if not all_results:
        print("No successful backtest results.", file=sys.stderr)
        return 1

    total_initial = sum(res.metrics.initial_capital for res in all_results)
    total_final = sum(res.metrics.final_asset for res in all_results)
    total_return = ((total_final - total_initial) / total_initial) * 100

    print("=" * 70)
    print("Portfolio Summary")
    print(f"Symbols         : {', '.join([r.symbol for r in all_results])}")
    print(f"Initial Capital : {total_initial:,.2f}")
    print(f"Final Asset     : {total_final:,.2f}")
    print(f"Return          : {total_return:.2f}%")

    save_trades_csv(all_trades, args.trade_csv)
    save_metrics_csv(all_results, args.metrics_csv)
    if args.grid_csv and all_grid_rows:
        path = Path(args.grid_csv)
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(all_grid_rows).to_csv(path, index=False)
        print(f"Grid saved: {path}")

    if args.save_chart:
        if len(all_results) == 1:
            plot_result(all_results[0], args.chart_file, args.show_chart)
        else:
            for res in all_results:
                symbol_chart = str(Path(args.chart_file).with_name(f"{res.symbol}_{Path(args.chart_file).name}"))
                plot_result(res, symbol_chart, show_chart=False)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
