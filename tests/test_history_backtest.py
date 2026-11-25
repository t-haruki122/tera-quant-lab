import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from app import database
from app.clients import yfinance_client
from app.schemas.stock import FinancialHistoryResponse
from app.services import stock_service


@pytest.mark.parametrize("interval,lookback", [("1wk", 365 * 5), ("1mo", 366 * 18)])
def test_aggregated_indicators_warmup_trimming_and_cache(monkeypatch, async_db, interval, lookback):
    engine, sessions = async_db
    start, end = "2024-01-01", "2024-02-29"
    fetched = []

    def external(symbol, from_date, to_date, source):
        assert source == "1d"
        assert from_date == (datetime(2024, 1, 1) - timedelta(days=lookback)).strftime("%Y-%m-%d")
        # Daily records with a trend and varying week/month lengths distinguish
        # SMA of aggregated closes from an SMA calculated on the daily source.
        rows = [dict(date=date.strftime("%Y-%m-%d"), open=i + 10, high=i + 12, low=i + 9,
                     close=i + 11, volume=100) for i, date in enumerate(pd.date_range(from_date, to_date, freq="B"))]
        fetched.extend(rows)
        return rows

    fake = Mock(side_effect=external)
    monkeypatch.setattr(stock_service._client, "get_history", fake)

    async def scenario():
        await database.init_db()

        async def request(symbol="x", requested_interval=interval, until=end):
            async with sessions() as session:
                return await stock_service.get_history(symbol, session, start, until, requested_interval)

        # Normalised symbol/interval and distinct sessions still share one flight.
        results = await asyncio.gather(*(request("x" if i % 2 else "X", interval.upper()) for i in range(3)))
        assert all(result == results[0] for result in results)
        fake.assert_called_once()
        result = results[0]
        aggregated = stock_service._aggregate_history(fetched, interval)
        closes = [row["close"] for row in aggregated]
        # Independent explicit expectations for all returned SMA windows.
        for row in result["history"]:
            assert start <= row["date"] <= end
            index = next(i for i, bar in enumerate(aggregated) if bar["date"] == row["date"])
            assert index >= 199
            for period in (25, 50, 75, 200):
                expected = round(sum(closes[index - period + 1:index + 1]) / period, 2)
                assert row[f"sma_{period}"] == expected
            assert row["rsi_14"] == 100  # Increasing aggregated closes.
        assert result["history"]
        # Cache reads must produce the same aggregated indicators, then trim.
        assert await request() == result
        fake.assert_called_once()
        result["history"][0]["close"] = -1
        assert (await request())["history"][0]["close"] > 0
        # Different interval/end-date is a distinct service flight despite the
        # shared daily cache. It must not receive another request's aggregate.
        daily, shorter = await asyncio.gather(request(requested_interval="1d"), request(until="2024-01-31"))
        assert len(daily["history"]) > len(shorter["history"])
        assert all(row["date"] <= "2024-01-31" for row in shorter["history"])
        fake.assert_called_once()
        await engine.dispose()

    asyncio.run(scenario())


def test_different_history_interval_keys_do_not_coalesce(monkeypatch, async_db):
    engine, sessions = async_db
    calls = []

    async def external(method, symbol, start, end, interval):
        calls.append(start)
        await asyncio.sleep(.01)
        return []

    monkeypatch.setattr(stock_service, "fetch", external)

    async def scenario():
        await database.init_db()

        async def request(interval, start="2024-01-01"):
            async with sessions() as session:
                return await stock_service.get_history("x", session, start, "2024-02-01", interval)

        await asyncio.gather(request("1wk"), request("1mo"), request("1wk", "2024-01-02"))
        assert len(calls) == len(set(calls)) == 3
        await engine.dispose()

    asyncio.run(scenario())


def test_financial_history_preserves_dates_eps_and_matching_shares(monkeypatch):
    # Direct diluted EPS, direct basic EPS, and NI / annual diluted shares.
    financials = pd.DataFrame({
        pd.Timestamp("2021-06-30"): [100, 20, 2, 10, 3, 8],
        pd.Timestamp("2022-06-30"): [200, 40, None, None, 4, 10],
        pd.Timestamp("2023-06-30"): [300, 60, None, 12, None, 15],
    }, index=["Total Revenue", "Net Income", "Diluted EPS", "Diluted Average Shares", "Basic EPS", "Basic Average Shares"])
    monkeypatch.setattr(yfinance_client.yf, "Ticker", lambda symbol: SimpleNamespace(info={"quoteType": "EQUITY"}, financials=financials))
    payload = yfinance_client.YFinanceClient().get_financial_history("x")
    rows = payload["history"]
    assert [(r["period"], r["period_end"], r["eps"], r["shares"]) for r in rows] == [
        ("2021", "2021-06-30", 2, 10), ("2022", "2022-06-30", 4, 10), ("2023", "2023-06-30", 5, 12)]
    # FastAPI's response schema must retain the new optional fields.
    assert FinancialHistoryResponse.model_validate(payload).model_dump() == payload
    limited = yfinance_client.YFinanceClient().get_financial_history("x", limit=1)
    assert limited["history"] == rows[-1:]


def test_financial_history_fields_and_cli_limit_through_http(client, monkeypatch):
    from app.services import finance_service

    payload = dict(symbol="X", history=[dict(period="2023", period_end="2023-06-30", eps=2,
                                           shares=10, revenue=100, net_income=20)])
    fake = Mock(return_value=payload)
    monkeypatch.setattr(finance_service._client, "get_financial_history", fake)
    response = client.get("/stock/X/financials/history?limit=10")
    assert response.status_code == 200 and response.json() == payload
    fake.assert_called_once_with("X", limit=10)
