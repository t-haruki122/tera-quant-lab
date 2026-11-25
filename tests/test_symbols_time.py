from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import Mock
from urllib.parse import quote

import pytest

from app.clients import yfinance_client
from app.services import stock_service
from app.timeutil import to_db, utc_iso, utcnow
from scripts.backtest_cli import APIClient


@pytest.mark.parametrize("symbol", ["^N225", "BRK.B", "7203.T"])
def test_encoded_stock_and_user_paths_and_timestamp_format(client, users, monkeypatch, symbol):
    timestamp = "2026-01-02T03:04:05.123456Z"
    fake = Mock(return_value={"symbol": symbol, "price": 123, "timestamp": timestamp})
    monkeypatch.setattr(stock_service._client, "get_current_price", fake)
    path = f"/stock/{quote(symbol, safe='')}"
    assert client.get(path).json() == {"symbol": symbol, "price": 123, "timestamp": timestamp}
    # Cache output must exactly match the external client's wire format.
    assert client.get(path).json()["timestamp"] == timestamp
    fake.assert_called_once_with(symbol)
    user = users[0]
    items = f"/user/{user['id']}/lists/{user['list_id']}/items"
    assert client.post(items, headers=user["headers"], json={"symbol": symbol.lower(), "tags": []}).status_code == 200
    encoded = quote(symbol.lower(), safe="")
    assert client.put(f"{items}/{encoded}/tags", headers=user["headers"], json={"tags": ["tag"]}).status_code == 200
    memo = f"/user/{user['id']}/memo/{encoded}"
    assert client.put(memo, headers=user["headers"], json={"memo": "test"}).status_code == 200
    assert client.get(memo, headers=user["headers"]).json()["memo"] == "test"
    assert client.delete(f"{items}/{encoded}", headers=user["headers"]).status_code == 200


@pytest.mark.parametrize("symbol", ["^N225", "BRK.B", "7203.T", "A/B?#"])
def test_cli_quotes_every_symbol_path(monkeypatch, symbol):
    client = APIClient("http://fake")
    request = Mock(return_value={})
    monkeypatch.setattr(client, "_request", request)
    client.get_history(symbol, "2024-01-01", "2024-02-01", "1d")
    client.get_financials(symbol)
    client.get_financial_history(symbol)
    assert [call.args[1] for call in request.call_args_list] == [
        f"/stock/{quote(symbol, safe='')}/history",
        f"/stock/{quote(symbol, safe='')}/financials",
        f"/stock/{quote(symbol, safe='')}/financials/history",
    ]


def test_utc_helpers_and_client_timestamp_compatibility(monkeypatch):
    value = datetime(2026, 1, 2, 12, 4, 5, 123456, tzinfo=timezone(timedelta(hours=9)))
    expected = datetime(2026, 1, 2, 3, 4, 5, 123456)
    assert utcnow().tzinfo is UTC
    assert to_db().tzinfo is None
    assert to_db(value) == expected
    assert utc_iso(value) == "2026-01-02T03:04:05.123456Z"
    assert utc_iso(expected) == "2026-01-02T03:04:05.123456Z"
    monkeypatch.setattr(yfinance_client, "utc_iso", lambda: utc_iso(value))
    ticker = Mock(info={"currentPrice": 1, "regularMarketPrice": 2})
    monkeypatch.setattr(yfinance_client.yf, "Ticker", lambda symbol: ticker)
    assert yfinance_client.YFinanceClient().get_current_price("aapl")["timestamp"] == utc_iso(value)
    assert yfinance_client.YFinanceClient().get_exchange_rate("usdjpy=x")["timestamp"] == utc_iso(value)
