"""Cached comparisons and fiscal valuations with offline provider fixtures."""

import asyncio
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest
from pydantic import SecretStr

from app import database
from app.models.db_models import CompanyProfile, Indicator, JQFinSummary, JQIssue, StockPrice, SymbolDirectoryEntry
from app.services import peer_service, stock_service
from app.timeutil import to_db
from tests.test_api_tokens import bearer, create_token


def _seed(client, *rows):
    async def persist():
        async with database.AsyncSessionLocal.begin() as db:
            db.add_all(rows)

    client.portal.call(persist)


def _annual(symbol, end, **values):
    return JQFinSummary(disc_no=f"{symbol}-{end}", code5=symbol[:4] + "0", symbol=symbol,
                        period_end=end, period_type="FY", disclosed_date=end + timedelta(days=40),
                        fetched_at=to_db(), **values)


def _price(symbol, day, close, interval="1d"):
    return StockPrice(symbol=symbol, interval=interval, timestamp=datetime.combine(day, datetime.min.time()), close=close)


def _metrics(payload):
    return {metric["key"]: metric for metric in payload["metrics"]}


def test_jpx_comparison_ranks_and_batches_only_missing_prices_once_per_sector_day(client, settings, monkeypatch):
    settings.jquants_api_key = SecretStr("offline-test-key")
    day = date(2026, 6, 12)
    monkeypatch.setattr(peer_service, "today_jst", lambda: day)
    symbols = [f"100{i}.T" for i in range(7)]
    rows = []
    for index, (symbol, roe) in enumerate(zip(symbols, [20, 20, 35, 50, 10, -5, 1], strict=True)):
        rows.extend((
            JQIssue(code5=symbol[:4] + "0", symbol=symbol, name=f"企業{index}",
                    sector33_name=None if index == 0 else "電気機器", as_of_date=day, updated_at=to_db()),
            _annual(symbol, date(2025, 3, 31), sales=120, operating_profit=12 if index < 3 else None, equity=100,
                    roe=0.2 if index == 0 else None, net_profit=-99 if index == 0 else roe, equity_ratio=0.5,
                    eps=-5 if index == 5 else 10, bps=-20 if index == 6 else 50, div_annual=2,
                    shares_outstanding_fy=1000 + index),
        ))
    rows.extend((
        SymbolDirectoryEntry(symbol=symbols[0], name="企業0", search_key="企業0", market="JP", sector="電気機器",
                             is_etf=False, source="jpx", updated_at=to_db()),
        _annual(symbols[0], date(2024, 3, 31), sales=100, eps=8, bps=40),
        _price(symbols[0], day, 100, "quote"),
        _price(symbols[1], day, 100),
    ))
    _seed(client, *rows)
    download = Mock(return_value=pd.DataFrame({("Close", s): [200 + i * 10, float("nan")]
                                              for i, s in enumerate(symbols[2:])}))
    monkeypatch.setattr("yfinance.download", download)
    cheap = client.get(f"/stock/{symbols[0]}/peers?prices=false").json()
    assert cheap["sector_name"] == "電気機器" and cheap["sector_source"] == "jpx33"
    assert cheap["peer_count"] == 7 and "12 週間遅れ" in cheap["notes"][0]
    metrics = _metrics(cheap)
    assert "per" not in metrics
    assert (metrics["roe"]["value"], metrics["roe"]["rank"], metrics["roe"]["percentile"],
            metrics["roe"]["median"], metrics["roe"]["count"]) == (20, 3, 66.7, 20, 7)
    assert (metrics["operating_margin"]["value"], metrics["operating_margin"]["count"],
            metrics["operating_margin"]["rank"], metrics["operating_margin"]["percentile"]) == (10, 3, None, None)
    assert metrics["sales_growth"]["value"] == pytest.approx(20)
    download.assert_not_called()

    async def simultaneous():
        async with database.AsyncSessionLocal() as first, database.AsyncSessionLocal() as second:
            return await asyncio.gather(peer_service.get_peer_comparison(symbols[0], first),
                                        peer_service.get_peer_comparison(symbols[1], second))

    first, second = client.portal.call(simultaneous)
    assert first.peers[0].symbol == symbols[6]
    assert next(m for m in second.metrics if m.key == "roe").rank == 3
    complete = client.get(f"/stock/{symbols[0]}/peers").json()
    metrics = _metrics(complete)
    assert (metrics["per"]["value"], metrics["per"]["count"], metrics["per"]["median"]) == (10, 6, 20.5)
    assert (metrics["pbr"]["value"], metrics["pbr"]["count"], metrics["pbr"]["median"]) == (2, 6, 4.1)
    assert (metrics["mix_index"]["value"], metrics["mix_index"]["count"]) == (20, 5)
    assert metrics["dividend_yield"]["value"] == 2
    by_symbol = {peer["symbol"]: peer for peer in complete["peers"]}
    assert by_symbol[symbols[5]]["per"] is None and by_symbol[symbols[5]]["pbr"] > 0
    assert by_symbol[symbols[6]]["pbr"] is None and by_symbol[symbols[6]]["per"] > 0
    download.assert_called_once()
    assert download.call_args.args == (symbols[2:],)
    assert download.call_args.kwargs["period"] == "5d" and not download.call_args.kwargs["threads"]
    day += timedelta(days=1)
    assert client.get(f"/stock/{symbols[1]}/peers").status_code == 200
    assert download.call_count == 2


def test_yahoo_peers_are_local_and_jp_falls_back_without_jquants(client):
    rows = []
    for index, symbol in enumerate(["AAPL", "B", "C", "D", "E", "F", "7203.T"]):
        rows.extend((CompanyProfile(symbol=symbol, name=symbol, sector="技術", industry="装置" if index < 5 else "他",
                                    market_cap=1000 + index, currency="JPY" if symbol.endswith(".T") else "USD"),
                     Indicator(symbol=symbol, roe=index, per=20 + index, pbr=2, profit_margin=10)))
    _seed(client, *rows)
    data = client.get("/stock/aapl/peers?prices=false").json()
    assert (data["sector_source"], data["sector_name"], data["peer_count"]) == ("yahoo_industry", "装置", 5)
    assert data["notes"] == ["取得済みの 5 銘柄で比較"]
    assert _metrics(data)["roe"]["percentile"] == 0
    assert _metrics(data)["profit_margin"]["percentile"] == 100
    fallback = client.get("/stock/7203.T/peers").json()
    assert fallback["sector_source"] == "yahoo_sector" and fallback["peer_count"] == 7
    assert fallback["notes"][0] == "J-Quants 未設定のため、取得済み銘柄のみで比較しています"


def _ticker(ends, *, statement="USD", trading="USD", incomes=(100, -50, 100, 100)):
    columns = pd.to_datetime(ends)
    return SimpleNamespace(
        info={"financialCurrency": statement, "currency": trading},
        financials=pd.DataFrame([list(incomes[:len(ends)]), [10] * len(ends)],
                                index=["Net Income", "Basic Average Shares"], columns=columns),
        balance_sheet=pd.DataFrame([[200] * len(ends), [20] * len(ends)],
                                    index=["Stockholders Equity", "Ordinary Shares Number"], columns=columns),
    )


def test_jp_valuation_history_merges_sources_and_uses_history_service_for_missing_close(client, settings, monkeypatch):
    settings.jquants_api_key = SecretStr("offline-test-key")
    ends = [date(year, 3, 31) for year in range(2021, 2026)]
    _seed(client, _annual("7203.T", ends[3], eps=-5, bps=10, roe=-0.1),
          _annual("7203.T", ends[4], eps=20, bps=-10, roe=0.25),
          Indicator(symbol="7203.T", per=14, pbr=1.5, mix_index=21),
          *[_price("7203.T", end + timedelta(days=1), 100) for end in ends[1:]])
    # Yahoo provides one older fiscal year and overlaps J-Quants on both recent years.
    ticker = Mock(return_value=_ticker([end.isoformat() for end in (ends[0], *ends[2:])], statement="JPY", trading="JPY"))
    monkeypatch.setattr("yfinance.Ticker", ticker)
    history = AsyncMock(return_value={"history": [{"date": "2021-04-01", "close": 120}]})
    monkeypatch.setattr(stock_service, "get_history", history)
    response = client.get("/stock/7203.T/valuation-history")
    assert response.status_code == 200, response.text
    data = response.json()
    oldest, older, loss, negative_equity = data["points"]
    assert [p["source"] for p in data["points"]] == ["yfinance", "yfinance", "jquants", "jquants"]
    assert (oldest["price_date"], oldest["eps"], oldest["bps"], oldest["per"]) == ("2021-04-01", 10, 10, 12)
    assert older["per"] is None and older["pbr"] == 10
    assert (loss["eps"], loss["per"], loss["roe"], loss["mix_index"]) == (-5, None, -10, None)
    assert negative_equity["pbr"] is None and negative_equity["per"] == 5
    assert data["band"]["per"] == {"min": 5, "median": 8.5, "max": 12}
    assert data["current"] == {"per": 14, "pbr": 1.5, "mix_index": 21}
    history.assert_awaited_once()
    assert history.call_args.args[2:] == ("2021-03-31", "2021-04-07")
    ticker.assert_called_once_with("7203.T")


@pytest.mark.parametrize(("symbol", "statement"), [("AAPL", "USD"), ("TM", "JPY")])
def test_yahoo_valuation_history_requires_cached_historical_fx_for_adr(client, monkeypatch, symbol, statement):
    ends = [date(2024, 3, 31), date(2025, 3, 31)]
    ticker = Mock(return_value=_ticker([end.isoformat() for end in ends], statement=statement, incomes=(100, 50)))
    monkeypatch.setattr("yfinance.Ticker", ticker)
    _seed(client, *[_price(symbol, end + timedelta(days=1), 10) for end in ends],
          _price("USDJPY=X", ends[0], 150), Indicator(symbol=symbol, per=8, pbr=2, mix_index=16))
    url = f"/stock/{symbol}/valuation-history"
    data = client.get(url).json()
    first, second = data["points"]
    assert data["price_currency"] == "USD" and first["statement_currency"] == statement
    assert first["source"] == second["source"] == "yfinance"
    if symbol == "TM":
        assert first["per"] == pytest.approx(150)
        assert second["per"] is None and second["pbr"] is None
        assert any("2025-03-31" in note and "為替レート" in note for note in data["notes"])
    else:
        assert first["per"] == 1 and second["per"] == 2
    assert client.get(url).json() == data
    ticker.assert_called_once_with(symbol)


def test_valuation_routes_validate_symbols_and_allow_read_tokens(client, users):
    headers = bearer(create_token(client, users[0])["token"])
    for endpoint, collection in (("peers", "metrics"), ("valuation-history", "points")):
        assert client.get(f"/stock/bad!/{endpoint}", headers=headers).status_code == 422
        url = f"/stock/%5En225/{endpoint}"
        response = client.get(url, headers=headers)
        assert response.status_code == 200 and response.json()["symbol"] == "^N225"
        assert response.json()[collection] == []
        assert client.get(url, headers=bearer("tql_invalid")).status_code == 401
    assert client.get("/stock/AAPL/peers?prices=perhaps", headers=headers).status_code == 422
