"""yfinance ラッパークライアント"""

import logging
import math
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd
import yfinance as yf

from app.clients.errors import external_call
from app.exceptions import SymbolNotFoundError
from app.timeutil import utc_iso

# Let I/O errors propagate instead of becoming empty price histories.
if hasattr(yf, "config"):
    yf.config.debug.hide_exceptions = False

logger = logging.getLogger("app.external.yfinance")


def _finite_history_value(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _market_time_fields(info: dict[str, Any]) -> dict[str, str]:
    epoch = _finite_history_value(info.get("regularMarketTime"))
    if epoch is None:
        return {}
    try:
        market_time = datetime.fromtimestamp(epoch, UTC)
    except (ValueError, OverflowError, OSError):
        return {}
    session_date = market_time.date()
    zone = info.get("exchangeTimezoneName")
    if isinstance(zone, str) and zone:
        try:
            session_date = market_time.astimezone(ZoneInfo(zone)).date()
        except (ValueError, OverflowError, ZoneInfoNotFoundError):
            pass
    return {"market_time": utc_iso(market_time), "market_date": session_date.isoformat()}


class YFinanceClient:
    """yfinanceライブラリを使用した株価・財務データ取得クライアント"""

    @external_call
    def get_current_price(self, symbol: str) -> dict[str, Any]:
        """
        現在の株価を取得

        Returns:
            {"symbol": str, "price": float, "timestamp": str}
        """
        logger.info("External API call: yfinance get_current_price symbol=%s", symbol.upper())
        ticker = yf.Ticker(symbol)
        info = ticker.info
        # 価格を取得（複数のフィールドをフォールバック）
        current = _finite_history_value(info.get("currentPrice") or info.get("regularMarketPrice"))
        previous = _finite_history_value(info.get("regularMarketPreviousClose") or info.get("previousClose"))
        price = current if current is not None else previous
        if price is None:
            raise SymbolNotFoundError(symbol)
        change = _finite_history_value(info.get("regularMarketChange"))
        change_pct = _finite_history_value(info.get("regularMarketChangePercent"))
        if current is not None and previous is not None and previous > 0:
            if change is None:
                change = current - previous
            if change_pct is None:
                change_pct = (current / previous - 1) * 100
        return {
            "symbol": symbol.upper(),
            "price": float(price),
            "timestamp": utc_iso(),
            "name": info.get("shortName") or info.get("longName"),
            "currency": info.get("currency"),
            "day_change": change,
            "day_change_pct": change_pct,
            **_market_time_fields(info),
        }

    @external_call
    def get_exchange_rate(self, pair: str) -> dict[str, Any]:
        """
        為替レートを取得
        Args:
            pair: 為替ペア (例: "USDJPY=X")
        Returns:
            {"pair": str, "rate": float, "timestamp": str}
        """
        logger.info("External API call: yfinance get_exchange_rate pair=%s", pair.upper())
        ticker = yf.Ticker(pair)
        info = ticker.info
        # ask, bid, regularMarketPrice などから価格を取得
        rate = (
            info.get("regularMarketPrice")
            or info.get("previousClose")
        )

        # yfinanceの仕様変更でinfoから取れない場合
        if rate is None:
            hist = ticker.history(period="1d")
            if not hist.empty:
                rate = float(hist["Close"].iloc[-1])
        if rate is None:
            raise SymbolNotFoundError(pair)
        timestamp = utc_iso()
        return {
            "pair": pair.upper(),
            "rate": float(rate),
            "timestamp": timestamp,
        }

    @external_call
    def get_history(
        self,
        symbol: str,
        start_date: str | None = None,
        end_date: str | None = None,
        interval: str = "1d",
    ) -> list[dict[str, Any]]:
        """
        過去の株価データを取得

        Args:
            symbol: ティッカーシンボル
            start_date: 開始日 (YYYY-MM-DD)
            end_date: 終了日 (YYYY-MM-DD)
            interval: データ間隔 (1d, 1wk, 1mo等)

        Returns:
            [{"date": str, "open": float, "high": float, "low": float,
              "close": float, "volume": int}, ...]
        """
        logger.info(
            "External API call: yfinance get_history symbol=%s interval=%s start=%s end=%s",
            symbol.upper(),
            interval,
            start_date,
            end_date,
        )
        ticker = yf.Ticker(symbol)
        # デフォルトは直近1ヶ月
        kwargs: dict[str, Any] = {"interval": interval}
        if start_date:
            kwargs["start"] = start_date
        if end_date:
            # The stock service uses an inclusive end date. Yahoo's end is exclusive.
            last_day = date.fromisoformat(end_date)
            if last_day < date.max:
                kwargs["end"] = (last_day + timedelta(days=1)).isoformat()
        if not start_date and not end_date:
            kwargs["period"] = "1mo"
        hist = ticker.history(**kwargs)
        if hist.empty:
            raise SymbolNotFoundError(symbol)
        records = []
        for idx, row in hist.iterrows():
            if not isinstance(idx, date):
                raise ValueError("Price history index must contain dates")
            close = _finite_history_value(row["Close"])
            if close is None:
                # Yahoo can return a partial current-day row with volume but no prices.
                continue
            open_price, high, low = (_finite_history_value(row[key]) for key in ("Open", "High", "Low"))
            raw_volume = row.at["Volume"]
            volume = _finite_history_value(raw_volume)
            record = {
                "date": idx.strftime("%Y-%m-%d"),
                "open": round(open_price, 2) if open_price else None,
                "high": round(high, 2) if high else None,
                "low": round(low, 2) if low else None,
                "close": round(close, 2),
                "volume": int(raw_volume) if volume else None,
            }
            records.append(record)
        return records

    @external_call
    def get_financials(self, symbol: str) -> dict[str, Any]:
        """
        財務情報を取得

        Returns:
            {"symbol": str, "revenue": int, "net_income": int,
             "eps": float, "pe_ratio": float}
        """
        logger.info("External API call: yfinance get_financials symbol=%s", symbol.upper())
        ticker = yf.Ticker(symbol)
        info = ticker.info
        if not info or info.get("quoteType") is None:
            raise SymbolNotFoundError(symbol)
        return {
            "symbol": symbol.upper(),
            "revenue": info.get("totalRevenue"),
            "net_income": info.get("netIncomeToCommon"),
            "eps": info.get("trailingEps"),
            "pe_ratio": info.get("trailingPE"),
        }

    @external_call
    def get_financial_history(self, symbol: str, limit: int = 6) -> dict[str, Any]:
        """
        年次の財務履歴（年度末日・EPS・加重平均株式数を含む）を取得

        Returns:
            history: period (year), period_end (ISO date), revenue, net_income,
            eps (Diluted EPS, otherwise Basic EPS), shares (matching weighted average).
        """
        logger.info(
            "External API call: yfinance get_financial_history symbol=%s limit=%s",
            symbol.upper(),
            limit,
        )
        ticker = yf.Ticker(symbol)
        info = ticker.info
        if not info or info.get("quoteType") is None:
            raise SymbolNotFoundError(symbol)
        financials = ticker.financials
        if financials is None or financials.empty:
            raise SymbolNotFoundError(symbol)
        entries: list[dict[str, Any]] = []
        columns = list(financials.columns)
        for col in columns:
            period_label = col.strftime("%Y") if isinstance(col, (datetime, date)) else str(col)
            period_end = col.strftime("%Y-%m-%d") if isinstance(col, (datetime, date)) else None

            def finite_value(label, column=col):
                if label not in financials.index:
                    return None
                value = financials.at[label, column]
                if value is None or not math.isfinite(float(value)):
                    return None
                return float(value)

            eps = finite_value("Diluted EPS")
            shares = finite_value("Diluted Average Shares")
            if eps is None:
                basic_eps = finite_value("Basic EPS")
                if basic_eps is not None or shares is None:
                    eps = basic_eps
                    shares = finite_value("Basic Average Shares")
            if eps is None and shares is not None and shares > 0:
                income = finite_value("Net Income")
                eps = income / shares if income is not None else None
            revenue = None
            net_income = None
            if "Total Revenue" in financials.index:
                raw_revenue = financials.at["Total Revenue", col]
                if raw_revenue is not None and not (isinstance(raw_revenue, float) and math.isnan(raw_revenue)):
                    revenue = int(raw_revenue)
            if "Net Income" in financials.index:
                raw_net_income = financials.at["Net Income", col]
                if raw_net_income is not None and not (isinstance(raw_net_income, float) and math.isnan(raw_net_income)):
                    net_income = int(raw_net_income)
            entries.append(
                {
                    "period": period_label,
                    "period_end": period_end,
                    "eps": eps,
                    "shares": shares,
                    "revenue": revenue,
                    "net_income": net_income,
                }
            )
        # yfinanceは新しい期間が先頭になりやすいので、古い順に整列
        entries.sort(key=lambda x: x["period"])
        if limit > 0:
            entries = entries[-limit:]
        return {"symbol": symbol.upper(), "history": entries}

    @external_call
    def get_dividend_history(self, symbol: str, limit: int = 20) -> dict[str, Any]:
        """
        配当履歴（年間1株配当 + 年利換算の配当利回り）を年次で取得

        Returns:
            {"symbol": str, "history": [{"date": str, "dividend_per_share": float, "dividend_yield": float | None, "year_end_close": float | None}, ...]}
        """
        logger.info(
            "External API call: yfinance get_dividend_history symbol=%s limit=%s",
            symbol.upper(),
            limit,
        )
        ticker = yf.Ticker(symbol)
        info = ticker.info
        if not info or info.get("quoteType") is None:
            raise SymbolNotFoundError(symbol)
        all_dividends = ticker.dividends
        if all_dividends is None or all_dividends.empty:
            raise SymbolNotFoundError(symbol)
        all_dividends = all_dividends.sort_index()
        dividend_index = all_dividends.index
        if not isinstance(dividend_index, pd.DatetimeIndex):
            raise ValueError("Dividend history index must contain dates")
        dividend_years = dividend_index.year
        years = sorted(set(dividend_years))
        if limit > 0:
            years = years[-limit:]
        first_year = years[0]
        last_year = years[-1]
        history_start = f"{first_year}-01-01"
        history_end = f"{last_year + 1}-01-10"
        price_hist = ticker.history(
            start=history_start,
            end=history_end,
            interval="1d",
            auto_adjust=False,
            actions=False,
        )
        year_end_close_by_year: dict[int, float] = {}
        if price_hist is not None and not price_hist.empty:
            try:
                closes = cast(pd.Series, price_hist["Close"].dropna())
                if not isinstance(closes.index, pd.DatetimeIndex):
                    raise ValueError("Price history index must contain dates")
                for year in years:
                    year_closes = closes.loc[closes.index.year == year]
                    if not year_closes.empty:
                        year_end_close_by_year[year] = float(year_closes.iloc[-1])
            except Exception:
                year_end_close_by_year = {}
        entries: list[dict[str, Any]] = []
        for year in years:
            year_dividends = all_dividends.loc[dividend_years == year]
            if year_dividends is None or year_dividends.empty:
                continue
            annual_dividend_per_share = float(year_dividends.sum())
            if math.isnan(annual_dividend_per_share):
                continue
            close_price = year_end_close_by_year.get(year)
            annual_dividend_yield = None
            if close_price is not None and close_price > 0:
                annual_dividend_yield = round((annual_dividend_per_share / close_price) * 100, 2)
            entries.append(
                {
                    "date": str(year),
                    "dividend_per_share": round(annual_dividend_per_share, 4),
                    "dividend_yield": annual_dividend_yield,
                    "year_end_close": round(close_price, 4) if close_price is not None else None,
                }
            )
        return {"symbol": symbol.upper(), "history": entries}

    @external_call
    def get_company_profile(self, symbol: str) -> dict[str, Any]:
        """
        会社プロフィールを取得

        Returns:
            {"symbol": str, "name": str, "sector": str, "industry": str,
             "employees": int, "summary": str, "market_cap": int, "currency": str,
             "website": str, "country": str}
        """
        logger.info("External API call: yfinance get_company_profile symbol=%s", symbol.upper())
        ticker = yf.Ticker(symbol)
        info = ticker.info
        if not info or info.get("quoteType") is None:
            raise SymbolNotFoundError(symbol)
        return {
            "symbol": symbol.upper(),
            "name": info.get("shortName") or info.get("longName") or symbol.upper(),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "employees": info.get("fullTimeEmployees"),
            "summary": info.get("longBusinessSummary"),
            "market_cap": info.get("marketCap"),
            "currency": info.get("currency"),
            "website": info.get("website"),
            "country": info.get("country"),
        }

    @external_call
    def get_indicators(self, symbol: str) -> dict[str, Any]:
        """
        投資指数を取得

        Returns:
            {"symbol": str, "roe": float, "roa": float, "pbr": float,
             "per": float, "eps": float, "dividend_yield": float,
             "mix_index": float, "profit_margin": float, "debt_to_equity": float}
        """
        logger.info("External API call: yfinance get_indicators symbol=%s", symbol.upper())
        ticker = yf.Ticker(symbol)
        info = ticker.info
        if not info or info.get("quoteType") is None:
            raise SymbolNotFoundError(symbol)
        roe = info.get("returnOnEquity")
        roa = info.get("returnOnAssets")
        pbr = info.get("priceToBook")
        per = info.get("trailingPE")
        eps = info.get("trailingEps")
        dividend_yield = info.get("dividendYield")
        profit_margin = info.get("profitMargins")
        debt_to_equity = info.get("debtToEquity")
        # ミックス指数 = PER × PBR（低いほど割安）
        mix_index = None
        if per is not None and pbr is not None:
            mix_index = round(per * pbr, 2)
        return {
            "symbol": symbol.upper(),
            "roe": round(roe * 100, 2) if roe is not None else None,
            "roa": round(roa * 100, 2) if roa is not None else None,
            "pbr": round(pbr, 2) if pbr is not None else None,
            "per": round(per, 2) if per is not None else None,
            "eps": round(eps, 2) if eps is not None else None,
            "dividend_yield": round(dividend_yield, 2) if dividend_yield is not None else None,
            "mix_index": mix_index,
            "profit_margin": round(profit_margin * 100, 2) if profit_margin is not None else None,
            "debt_to_equity": round(debt_to_equity, 2) if debt_to_equity is not None else None,
        }
