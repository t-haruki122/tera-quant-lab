"""Watchlist holdings, cached valuation and progressive stock snapshots."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from app import database
from app.exceptions import UpstreamUnavailableError
from app.models.db_models import CompanyProfile, Indicator, StockMemo, StockPrice
from app.services import forex_service, indicator_service, profile_service, stock_service
from app.timeutil import to_db, utc_iso


def list_path(user):
    return f"/user/{user['id']}/lists/{user['list_id']}"


def add_item(client, user, symbol, shares=None):
    path = list_path(user)
    assert client.post(f"{path}/items", headers=user["headers"], json={"symbol": symbol}).status_code == 200
    if shares is not None:
        response = client.put(f"{path}/items/{symbol}/shares", headers=user["headers"], json={"shares": shares})
        assert response.json() == {"shares": shares}


def test_shares_and_cash_validation_ownership_and_clearing(client, users):
    alice, bob = users
    path = list_path(alice)
    headers = alice["headers"]
    add_item(client, alice, "AAPL")
    shares = f"{path}/items/aapl/shares"
    cash = f"{path}/cash"

    for url, body in [(shares, {"shares": 3.1254}), (cash, {"cash_jpy": 1_000_000})]:
        assert client.put(url, json=body).status_code == 401
        assert client.put(url, headers=bob["headers"], json=body).status_code == 403
        alien = url.replace(f"/lists/{alice['list_id']}", f"/lists/{bob['list_id']}")
        assert client.put(alien, headers=headers, json=body).status_code == 404
        assert client.put(url, headers=headers, json=body).json() == body
    assert client.put(f"{path}/items/MSFT/shares", headers=headers, json={"shares": 1}).status_code == 404

    for value in (0, -1, 1e9 + 1, 0.00001, 1.23456, "1", True):
        result = client.put(shares, headers=headers, json={"shares": value})
        assert result.status_code == 422
        assert "株数" in result.json()["detail"][0]["msg"]
    for value in (-1, 10**13 + 1, 1.0, 0.1, "1", True):
        result = client.put(cash, headers=headers, json={"cash_jpy": value})
        assert result.status_code == 422
        assert "現金" in result.json()["detail"][0]["msg"]
    for url in (shares, cash):
        assert client.put(url, headers=headers, json={}).status_code == 422
    assert client.put(shares, headers=headers, json={"shares": 0.0001}).status_code == 200

    detail = client.get(path, headers=headers).json()
    assert detail["items"][0]["shares"] == 0.0001 and detail["cash_jpy"] == 1_000_000
    assert client.put(shares, headers=headers, json={"shares": None}).json() == {"shares": None}
    assert client.put(cash, headers=headers, json={"cash_jpy": 0}).status_code == 200
    assert client.get(f"{path}/summary?cached=true", headers=headers).json()["portfolio"] is None
    assert client.put(cash, headers=headers, json={"cash_jpy": None}).json() == {"cash_jpy": None}
    detail = client.get(path, headers=headers).json()
    assert detail["items"][0]["shares"] is None and detail["cash_jpy"] is None


def test_cash_only_and_removed_portfolio_routes(client, users):
    alice, _ = users
    path = list_path(alice)
    headers = alice["headers"]
    assert client.get(path + "/summary?cached=true", headers=headers).json()["portfolio"] is None
    client.put(path + "/cash", headers=headers, json={"cash_jpy": 10**13})
    data = client.get(path + "/summary?cached=true", headers=headers).json()
    assert data["items"] == []
    p = data["portfolio"]
    assert p["total_value_jpy"] == 10**13 and p["cash_ratio_pct"] == 100
    assert p["by_currency"][0] == {"label": "JPY", "value_jpy": 10**13, "pct": 100}
    assert p["concentration"] == {"largest": None, "top_5_pct": None, "holding_count": 0}
    for method, suffix in [("GET", ""), ("POST", ""), ("PUT", "/1"), ("DELETE", "/1"), ("GET", "/export.csv")]:
        assert client.request(method, f"/user/{alice['id']}/portfolio{suffix}", headers=headers).status_code == 404


def test_cached_summary_values_holdings_without_provider_calls(client, users, monkeypatch, settings):
    alice, _ = users
    path, headers = list_path(alice), alice["headers"]
    for symbol, shares in [("7203.T", 4), ("AAPL", 1.5), ("JPY2", 2), ("EURTEST", 1), ("MISSING", 1), ("WATCH", None)]:
        add_item(client, alice, symbol, shares)
    client.put(f"{path}/cash", headers=headers, json={"cash_jpy": 2500})
    settings.cache_stale_max_days = 1
    fresh = to_db()
    old = fresh - timedelta(days=90)
    fx_time = fresh - timedelta(hours=2)

    async def seed():
        async with database.AsyncSessionLocal() as db:
            db.add_all([
                StockPrice(symbol="7203.T", interval="quote", timestamp=fresh, close=500, quote_currency="JPY"),
                StockPrice(symbol="AAPL", interval="quote", timestamp=old, close=20, quote_currency="USD", cached_at=old),
                StockPrice(symbol="AAPL", interval="1d", timestamp=fresh, close=1234),
                StockPrice(symbol="JPY2", interval="quote", timestamp=fresh, close=500, quote_currency="JPY"),
                StockPrice(symbol="EURTEST", interval="quote", timestamp=fresh, close=20, quote_currency="EUR"),
                StockPrice(symbol="USDJPY=X", interval="forex", timestamp=fx_time, close=150, cached_at=fx_time),
                CompanyProfile(symbol="AAPL", name="Apple", sector="技術", currency="JPY"),
                CompanyProfile(symbol="7203.T", name="会社", sector="輸送"),
                Indicator(symbol="AAPL", per=4, pbr=-2, mix_index=-8, dividend_yield=2),
                StockMemo(user_id=alice["id"], symbol="aapl", memo="旧銘柄メモ"),
            ])
            await db.commit()

    client.portal.call(seed)
    blocked = AsyncMock(side_effect=AssertionError("cache-only called a provider"))
    for service, method in [(stock_service, "get_current_price"), (profile_service, "get_profile"),
                            (indicator_service, "get_indicators"), (forex_service, "get_exchange_rate")]:
        monkeypatch.setattr(service, method, blocked)
    response = client.get(f"{path}/summary?cached=true", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()
    jp, us, _, eur, missing, watch = data["items"]
    assert jp["value_jpy"] == 2000 and jp["weight_pct"] == 20
    assert jp["pending"] and jp["mix_index"] is None
    assert us["price"] == 20 and us["currency"] == "USD" and us["value_jpy"] == 4500
    assert us["shares"] == 1.5 and us["weight_pct"] == 45
    assert us["stale"] and us["cached_at"] == utc_iso(old) and not us["pending"]
    assert us["mix_index"] is None
    assert us["memo"] == "旧銘柄メモ"
    assert eur["value_jpy"] is None and "EUR" in eur["warning"]
    assert missing["price"] is None and missing["pending"] and missing["value_jpy"] is None
    assert watch["shares"] is None and watch["weight_pct"] is None
    p = data["portfolio"]
    assert (p["total_value_jpy"], p["stocks_value_jpy"], p["cash_jpy"], p["cash_ratio_pct"]) == (10000, 7500, 2500, 25)
    assert [(row["label"], row["value_jpy"], row["pct"]) for row in p["by_region"]] == [
        ("日本株", 3000, 30), ("米国株", 4500, 45), ("その他", 0, 0), ("現金", 2500, 25),
    ]
    assert [(row["label"], row["value_jpy"], round(row["pct"])) for row in p["by_currency"]] == [
        ("JPY", 5500, 55), ("USD", 4500, 45),
    ]
    assert [(row["label"], row["value_jpy"]) for row in p["by_sector"]] == [
        ("技術", 4500), ("輸送", 2000), ("不明", 1000),
    ]
    assert [row["pct"] for row in p["by_sector"]] == pytest.approx([60, 2000 / 75, 1000 / 75])
    assert p["concentration"] == {"largest": {"symbol": "AAPL", "pct": 45}, "top_5_pct": 75, "holding_count": 5}
    assert p["usd_jpy"] == {"rate": 150, "stale": True, "cached_at": utc_iso(fx_time)}
    assert [row["symbol"] for row in p["excluded"]] == ["EURTEST", "MISSING"]
    blocked.assert_not_awaited()

    async def drop_rate():
        async with database.AsyncSessionLocal() as db:
            row = await forex_service.read_cached_rate(forex_service.USD_JPY, db)
            await db.delete(row)
            await db.commit()

    client.portal.call(drop_rate)
    result = client.get(f"{path}/summary?cached=true", headers=headers).json()["portfolio"]
    assert result["total_value_jpy"] == 5500 and result["usd_jpy"] is None
    assert "USD/JPY" in next(row["reason"] for row in result["excluded"] if row["symbol"] == "AAPL")
    blocked.assert_not_awaited()


def test_snapshot_fetches_components_concurrently_and_preserves_partial_data(client, monkeypatch):
    ready = 0

    def source(outcome):
        async def get(symbol, db):
            nonlocal ready
            assert symbol == "AAPL"
            ready += 1
            for _ in range(20):
                if ready == 3:
                    break
                await asyncio.sleep(0)
            assert ready == 3
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return get

    monkeypatch.setattr(stock_service, "get_current_price", source({"price": 100, "currency": "USD"}))
    monkeypatch.setattr(profile_service, "get_profile", source(UpstreamUnavailableError()))
    monkeypatch.setattr(indicator_service, "get_indicators", source({"pbr": 2, "per": 4, "mix_index": 8}))
    result = client.get("/stock/aapl/snapshot")
    assert result.status_code == 200 and ready == 3
    assert result.json() == {
        "symbol": "AAPL", "price": 100, "currency": "USD", "name": None, "mix_index": 8,
        "dividend_yield": None, "pbr": 2, "per": 4, "error": "一部のデータを取得できませんでした。",
    }
    assert client.get("/stock/a!pl/snapshot").status_code == 422
