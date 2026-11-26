from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import quote

import pandas as pd
import pytest
from sqlalchemy import update

from app import database
from app.exceptions import ExternalAPIError, SymbolNotFoundError
from app.market_indicators import INDICATORS
from app.models.db_models import StockPrice
from app.timeutil import to_db, utcnow

QUOTES = {
    "^N225": (39_123.45, 39_000),
    "^GSPC": (5_900, 5_920),
    "GC=F": (2_345.6, 2_315.6),
    "^TNX": (4.25, 4.2),
    "USDJPY=X": (155.23, 154.25),
}


@pytest.fixture
def market_provider(monkeypatch):
    today = utcnow().date()
    dates = pd.date_range(today - timedelta(days=59), periods=60)

    def ticker(symbol):
        current, previous = QUOTES.get(symbol, (100, 100))
        info = {
            "regularMarketPrice": current, "regularMarketPreviousClose": previous,
            "regularMarketTime": int(utcnow().timestamp()), "exchangeTimezoneName": "UTC",
        }
        # Intentionally omit equity fields and currency to exercise index/futures handling.
        frame = pd.DataFrame(
            {"Open": range(1, 61), "High": range(1, 61), "Low": range(1, 61),
             "Close": range(1, 61), "Volume": [None] * 60},
            index=dates,
        )
        return SimpleNamespace(info=info, history=Mock(return_value=frame))

    provider = Mock(side_effect=ticker)
    monkeypatch.setattr("yfinance.Ticker", provider)
    return provider, ticker


def indicators(response):
    assert response.status_code == 200
    data = response.json()
    assert data["as_of"].endswith("Z")
    assert [row["symbol"] for row in data["indicators"]] == [item.symbol for item in INDICATORS]
    return {row["symbol"]: row for row in data["indicators"]}


def test_overview_reuses_forex_and_history_and_reports_changes(client, market_provider):
    provider, _ = market_provider
    assert client.get("/forex/USDJPY").json()["rate"] == 155.23
    rows = indicators(client.get("/market/overview?cached=false"))
    nikkei = rows["^N225"]
    assert nikkei["value"] == 39_123.45
    assert nikkei["previous_close"] == 39_000
    assert nikkei["change"] == pytest.approx(123.45)
    assert nikkei["change_pct"] == pytest.approx(123.45 / 39_000 * 100)
    assert rows["^GSPC"]["change"] == -20
    assert rows["1306.T"]["name"] == "TOPIX（1306.T ETFで代用）"
    assert rows["GC=F"]["unit"] == "usd_per_oz"
    assert rows["GC=F"]["note"] == "先物"
    assert rows["JPY=X"]["previous_close"] == 154.25
    assert rows["JPY=X"]["value"] == 155.23
    assert rows["^TNX"]["value"] == 4.25
    assert rows["^TNX"]["unit"] == "yield_pct" and rows["^TNX"]["currency"] is None
    assert rows["^TNX"]["change_pt"] == pytest.approx(0.05)
    assert "change_pct" not in rows["^TNX"] and "change_pt" not in nikkei
    for row in rows.values():
        assert row["sparkline"] == list(range(31, 61))
        assert row["as_of"].endswith("Z")
        assert row["error"] is None and row["stale"] is False
    requested = [call.args[0] for call in provider.call_args_list]
    assert requested.count("USDJPY=X") == 1 and requested.count("JPY=X") == 1
    assert client.get("/stock/JPY%3DX").json()["price"] == 155.23

    provider.reset_mock()
    assert indicators(client.get("/market/overview")) == rows
    assert indicators(client.get("/market/overview?cached=true")) == rows
    provider.assert_not_called()


def test_cached_only_includes_expired_db_rows_and_cold_skeleton_metadata(client, market_provider):
    provider, _ = market_provider
    rows = indicators(client.get("/market/overview?cached=true"))
    assert all(row["value"] is None and row["sparkline"] == [] and row["error"] is None for row in rows.values())
    provider.assert_not_called()
    client.get("/market/overview")
    provider.reset_mock()

    async def expire():
        async with database.AsyncSessionLocal() as session:
            await session.execute(update(StockPrice).where(
                StockPrice.symbol.in_(("^N225", "USDJPY=X")),
            ).values(cached_at=to_db(utcnow() - timedelta(days=30))))
            await session.commit()

    client.portal.call(expire)
    rows = indicators(client.get("/market/overview?cached=true"))
    assert rows["^N225"]["stale"] is True and rows["^N225"]["sparkline"] == list(range(31, 61))
    assert rows["JPY=X"]["stale"] is True and rows["JPY=X"]["value"] == 155.23
    assert rows["GC=F"]["stale"] is False
    provider.assert_not_called()


def test_provider_failure_is_isolated_and_previous_close_can_use_history(client, market_provider):
    provider, ticker = market_provider

    def fail_one(symbol):
        if symbol == "GC=F":
            raise SymbolNotFoundError(symbol)
        mock = ticker(symbol)
        if symbol == "^N225":
            del mock.info["regularMarketPreviousClose"]
        return mock

    provider.side_effect = fail_one
    rows = indicators(client.get("/market/overview"))
    assert rows["GC=F"]["value"] is None and rows["GC=F"]["sparkline"] == []
    assert "現在値" in rows["GC=F"]["error"] and "日次履歴" in rows["GC=F"]["error"]
    assert rows["^N225"]["previous_close"] == 59
    assert rows["^GSPC"]["value"] == 5900 and rows["^GSPC"]["error"] is None


def test_refresh_uses_existing_stale_quote_and_history_on_transient_failure(client, market_provider):
    provider, ticker = market_provider
    client.get("/market/overview")

    async def expire():
        async with database.AsyncSessionLocal() as session:
            await session.execute(update(StockPrice).where(StockPrice.symbol == "^N225").values(
                cached_at=to_db(utcnow() - timedelta(days=2)),
            ))
            await session.commit()

    client.portal.call(expire)

    def fail_one(symbol):
        if symbol == "^N225":
            raise ExternalAPIError("Temporary upstream error")
        return ticker(symbol)

    provider.side_effect = fail_one
    rows = indicators(client.get("/market/overview"))
    assert rows["^N225"]["value"] == 39_123.45
    assert rows["^N225"]["sparkline"] == list(range(31, 61))
    assert rows["^N225"]["stale"] is True and "現在値" in rows["^N225"]["error"]
    assert rows["^GSPC"]["stale"] is False and rows["^GSPC"]["error"] is None


@pytest.mark.parametrize(("symbol", "currency", "unit"), [
    ("^N225", "JPY", "points"), ("^GSPC", "USD", "points"), ("GC=F", "USD", "usd_per_oz"),
    ("EURJPY=X", "JPY", "jpy"), ("BTC-USD", "USD", "usd"), ("^TNX", None, "yield_pct"),
])
def test_market_ticker_quote_snapshot_indicators_and_history(client, market_provider, symbol, currency, unit):
    path = f"/stock/{quote(symbol, safe='')}"
    price = client.get(path)
    assert price.status_code == 200
    assert price.json().get("currency") == currency and price.json()["unit"] == unit
    snapshot = client.get(f"{path}/snapshot")
    assert snapshot.status_code == 200
    assert snapshot.json()["currency"] == currency and snapshot.json().get("error") is None
    fundamentals = client.get(f"{path}/indicators")
    assert fundamentals.status_code == 200
    assert all(fundamentals.json()[field] is None for field in ("per", "pbr", "dividend_yield", "mix_index"))
    history = client.get(f"{path}/history")
    assert history.status_code == 200 and history.json()["history"][-1]["close"] == 60
    assert history.json()["unit"] == unit


def test_market_overview_uses_public_read_limit(client, settings):
    settings.rate_limit_enabled = True
    settings.rate_limit_data_requests = 1
    assert client.get("/market/overview?cached=true").status_code == 200
    assert client.get("/market/overview?cached=true").status_code == 429
