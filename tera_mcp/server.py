"""Read-only MCP tools backed by the tera-quant-lab HTTP API."""

import asyncio
import re
from datetime import timedelta
from typing import Annotated, Any, Literal
from urllib.parse import quote

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from app.timeutil import utcnow
from app.validation import HistoryInterval, SQLiteID, normalize_symbol, shift_years
from tera_mcp.client import JSONObject, TQLClient

INSTRUCTIONS = """\
データは利用者のローカルアプリに由来します。yfinance などから取得したキャッシュには遅延や欠損があり得ます。
stale と cached_at を確認し、数値を引用するときは銘柄と日付を添えてください。
赤字や債務超過などで PER または PBR が 0 以下の場合、mix_index は null です。利益が極端に小さい場合、PER は非常に大きくなります。
保有株数・現金・構成比は get_watchlist で取得できます。取得原価・損益は設計上記録しません。集計の除外銘柄も確認してください。
分析、論点整理、リスクの指摘を行い、売買の最終判断は利用者が行う前提で答えてください。
読み取り専用です。書き込み操作はできません。バックテストの実行結果は保存されません。
"""

LOCAL_READ = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
MARKET_READ = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)


def _symbol(value: str) -> str:
    try:
        return normalize_symbol(value)
    except ValueError as exc:
        raise ToolError(str(exc)) from None


def _sample[T](rows: list[T], limit: int) -> list[T]:
    if len(rows) <= limit:
        return rows
    return [rows[index * (len(rows) - 1) // (limit - 1)] for index in range(limit)]


def _price_summary(bars: list[JSONObject]) -> JSONObject:
    summary: JSONObject = dict.fromkeys(("first_date", "first_close", "last_date", "last_close", "change_pct",
                                         "high", "high_date", "low", "low_date"))
    summary["bar_count"] = len(bars)
    if not bars:
        return summary
    first, last = bars[0], bars[-1]
    high = max((bar for bar in bars if bar.get("high") is not None), key=lambda bar: bar["high"], default=None)
    low = min((bar for bar in bars if bar.get("low") is not None), key=lambda bar: bar["low"], default=None)
    return {
        "bar_count": len(bars),
        "first_date": first["date"], "first_close": first["close"],
        "last_date": last["date"], "last_close": last["close"],
        "change_pct": (last["close"] / first["close"] - 1) * 100 if first["close"] else None,
        "high": high["high"] if high else None, "high_date": high["date"] if high else None,
        "low": low["low"] if low else None, "low_date": low["date"] if low else None,
    }


def _compact_backtest(result: JSONObject) -> JSONObject:
    compact = dict(result)
    if isinstance(curve := result.get("equity_curve"), list):
        compact["equity_curve"] = _sample(curve, 100)
    if isinstance(trades := result.get("trades"), list):
        compact["trade_count"] = len(trades)
        compact["trades"] = trades[-20:]
    return compact


def create_server(client: TQLClient) -> MCPServer[None]:
    server: MCPServer[None] = MCPServer("tera-quant-lab", instructions=INSTRUCTIONS, log_level="WARNING")

    @server.tool(annotations=LOCAL_READ)
    async def list_watchlists() -> dict[str, Any]:
        """ログイン中の利用者のウォッチリスト一覧と既定のリスト ID を取得します。"""
        return await client.get_user("/lists")

    @server.tool(annotations=MARKET_READ)
    async def get_watchlist(list_id: SQLiteID | None = None) -> dict[str, Any]:
        """ウォッチリストの株価、指標、タグ、メモ、保有株数、円換算評価額、現金と構成比を取得します。ID を省略すると既定のリストを使います。"""
        if list_id is None:
            list_id = (await list_watchlists()).get("default_list_id")
            if list_id is None:
                raise ToolError("既定のウォッチリストがありません。")
        return await client.get_user(f"/lists/{list_id}/summary")

    @server.tool(annotations=LOCAL_READ)
    async def get_price_alerts() -> dict[str, Any]:
        """設定済みの価格アラートと履歴情報を取得します。"""
        return await client.get_user("/alerts")

    @server.tool(annotations=MARKET_READ)
    async def get_stock(symbol: str) -> dict[str, Any]:
        """銘柄の現在株価、企業プロフィール、PER・PBR などの投資指標を取得します。"""
        symbol = _symbol(symbol)
        path = f"/stock/{quote(symbol, safe='')}"
        parts = {"price": path, "profile": f"{path}/profile", "indicators": f"{path}/indicators"}

        async def fetch_part(url: str) -> JSONObject:
            try:
                return await client.get(url)
            except ToolError as exc:
                return {"error": str(exc)}

        results = await asyncio.gather(*(fetch_part(url) for url in parts.values()))
        return {"symbol": symbol, **dict(zip(parts, results, strict=True))}

    @server.tool(annotations=MARKET_READ)
    async def get_financials(symbol: str, include_history: bool = True) -> dict[str, Any]:
        """売上高、純利益、EPS などの財務情報と、必要に応じて過去の年次財務を取得します。"""
        path = f"/stock/{quote(_symbol(symbol), safe='')}/financials"
        result = {"financials": await client.get(path)}
        if include_history:
            result["history"] = await client.get(f"{path}/history")
        return result

    @server.tool(annotations=MARKET_READ)
    async def get_price_history(
        symbol: str, start_date: str | None = None, end_date: str | None = None, interval: HistoryInterval = "1d",
    ) -> dict[str, Any]:
        """株価履歴の統計と最大120件の終値を返します。日付は YYYY-MM-DD 形式で、両方を省略すると直近1年です。"""
        path = f"/stock/{quote(_symbol(symbol), safe='')}/history"
        if start_date is None and end_date is None:
            today = utcnow().date()
            start = shift_years(today, -1)
            if shift_years(start, 1) < today:
                # Align with the API's maximum-span check on February 29.
                start += timedelta(days=1)
            start_date, end_date = start.isoformat(), today.isoformat()
        params = {"interval": interval}
        if start_date is not None:
            params["start_date"] = start_date
        if end_date is not None:
            params["end_date"] = end_date
        data = await client.get(path, params=params)
        bars = sorted(data.pop("history"), key=lambda bar: bar["date"])
        return {
            **data, "interval": interval, "summary": _price_summary(bars),
            "history": [{"date": bar["date"], "close": bar["close"]} for bar in _sample(bars, 120)],
        }

    @server.tool(annotations=MARKET_READ)
    async def get_news(symbol: str, limit: int = 10) -> dict[str, Any]:
        """銘柄のニュースの見出し、発行元、公開日時（UTC）、リンクを最大30件取得します。"""
        data = await client.get(f"/stock/{quote(_symbol(symbol), safe='')}/news")
        fields = ("title", "publisher", "published_at", "url")
        data["news"] = [{key: article.get(key) for key in fields} for article in data["news"][:max(1, min(limit, 30))]]
        return data

    @server.tool(annotations=MARKET_READ)
    async def get_exchange_rate(pair: str = "USDJPY") -> dict[str, Any]:
        """為替レートを取得します。通貨ペアは USDJPY または USDJPY=X の形式で指定します。"""
        pair = pair.strip().upper()
        if not re.fullmatch(r"[A-Z]{6}(?:=X)?", pair):
            raise ToolError("為替ペアは USDJPY または USDJPY=X のように英字6文字で指定してください")
        return await client.get(f"/forex/{quote(pair, safe='')}")

    @server.tool(annotations=LOCAL_READ)
    async def list_backtest_strategies() -> dict[str, Any]:
        """バックテストで利用できる戦略、パラメーター、評価指標を取得します。"""
        return await client.get("/backtest/strategies")

    @server.tool(annotations=MARKET_READ)
    async def run_backtest(
        symbol: str,
        start_date: str,
        end_date: str,
        strategy: str = "sma_cross",
        params: JSONObject | None = None,
        interval: HistoryInterval = "1d",
        execution: Literal["next_open", "close"] = "next_open",
        fee_rate: Annotated[float, Field(ge=0, le=0.1)] = 0,
        slippage_bps: Annotated[float, Field(ge=0, le=1000)] = 0,
        initial_capital: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 1_000_000,
        eps_lag_days: Annotated[int, Field(ge=0, le=730)] = 90,
        risk_free: Annotated[float, Field(ge=-0.5, le=1)] = 0,
    ) -> dict[str, Any]:
        """指定した銘柄と戦略でバックテストを計算します。日付は YYYY-MM-DD 形式で、実行結果は保存されません。"""
        result = await client.run_backtest({
            "symbol": _symbol(symbol), "start_date": start_date, "end_date": end_date,
            "strategy": strategy, "params": params if params is not None else {}, "interval": interval,
            "execution": execution, "fee_rate": fee_rate, "slippage_bps": slippage_bps,
            "initial_capital": initial_capital, "eps_lag_days": eps_lag_days, "risk_free": risk_free,
        })
        return _compact_backtest(result)

    @server.tool(annotations=LOCAL_READ)
    async def list_saved_backtests() -> dict[str, Any]:
        """ログイン中の利用者が保存したバックテストの一覧と成績の概要を取得します。"""
        return await client.get_saved_run()

    @server.tool(annotations=LOCAL_READ)
    async def get_saved_backtest(run_id: SQLiteID) -> dict[str, Any]:
        """保存したバックテストの詳細を取得します。資産曲線は最大100点、約定履歴は保存分の末尾20件までです。"""
        data = await client.get_saved_run(f"/{run_id}")
        data["result"] = _compact_backtest(data["result"])
        return data

    return server
