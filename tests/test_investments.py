import asyncio
import csv
import io
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.clients import yfinance_client
from app.exceptions import ExternalAPIError, RateLimitError, SymbolNotFoundError, UpstreamUnavailableError
from app.services import forex_service, price_alert_service, stock_service
from app.timeutil import utc_iso


def endpoint(user, section):
    return f"/user/{user['id']}/{section}"


def add_holding(client, user, symbol, **fields):
    response = client.post(endpoint(user, "portfolio"), headers=user["headers"], json={
        "symbol": symbol, "shares": 2, "avg_cost": 100, **fields,
    })
    assert response.status_code == 201, response.text
    return response.json()


def add_alert(client, user, symbol, **fields):
    response = client.post(endpoint(user, "alerts"), headers=user["headers"], json={
        "symbol": symbol, "kind": "above", "threshold": 100, **fields,
    })
    assert response.status_code == 201, response.text
    return response.json()


def quote(symbol, price, currency="USD", **fields):
    return {"symbol": symbol, "price": price, "name": f"{symbol} 社", "currency": currency, **fields}


@pytest.fixture(autouse=True)
def mock_quote_provider(monkeypatch):
    monkeypatch.setattr(stock_service._client, "get_current_price",
                        lambda symbol: quote(symbol, 120, timestamp=utc_iso()))


def test_holdings_crud_validation_ownership_and_csv(client, users, settings, monkeypatch):
    alice, bob = users
    path, headers = endpoint(alice, "portfolio"), alice["headers"]
    assert client.get(path).status_code == 401
    assert client.get(path, headers=bob["headers"]).status_code == 403
    settings.portfolio_max_holdings = 1
    initial = {"symbol": " aapl ", "shares": 1, "avg_cost": 0}
    for invalid in (
        {"symbol": "a/b"}, {"shares": 0}, {"shares": -2}, {"shares": True}, {"avg_cost": -0.01},
        {"acquired_on": "2026-02-30"}, {"note": "株" * 2001},
    ):
        failed = client.post(path, headers=headers, json=initial | invalid)
        assert failed.status_code == 422
        assert "してください" in str(failed.json()["detail"]) or "正しくありません" in str(failed.json()["detail"])
    holding = add_holding(client, alice, " aapl ", avg_cost=0)
    assert holding["symbol"] == "AAPL" and holding["created_at"].endswith("Z")
    duplicate = client.post(path, headers=headers, json=initial)
    assert duplicate.status_code == 409 and "登録済み" in duplicate.json()["detail"]
    limit = client.post(path, headers=headers, json=initial | {"symbol": "MSFT"})
    assert limit.status_code == 400 and "1 件まで" in limit.json()["detail"]

    changes = {"shares": 1.5, "avg_cost": 180, "acquired_on": "2026-11-25", "note": "=HYPERLINK(1)"}
    item = f"{path}/{holding['id']}"
    foreign = f"{endpoint(bob, 'portfolio')}/{holding['id']}"
    assert client.put(foreign, headers=bob["headers"], json=changes).status_code == 404
    assert client.delete(foreign, headers=bob["headers"]).status_code == 404
    updated = client.put(item, headers=headers, json=changes)
    assert updated.status_code == 200
    assert updated.json() == holding | changes | {"updated_at": updated.json()["updated_at"]}
    assert updated.json()["updated_at"] > holding["updated_at"]

    csv_response = client.get(path + "/export.csv", headers=headers)
    assert csv_response.content.startswith(b"\xef\xbb\xbf")
    assert list(csv.DictReader(io.StringIO(csv_response.content.decode("utf-8-sig")))) == [{
        "symbol": "AAPL", "shares": "1.5", "avg_cost": "180.0", "acquired_on": "2026-11-25", "note": "\t=HYPERLINK(1)",
    }]
    monkeypatch.setattr(stock_service, "get_current_price", AsyncMock(return_value=quote("AAPL", 190, "JPY")))
    assert client.get(path, headers=headers).json()["holdings"][0]["acquired_on"] == "2026-11-25"
    assert client.delete(item, headers=headers).status_code == 204
    assert client.delete(item, headers=headers).status_code == 404
    assert client.get(path, headers=headers).json()["holdings"] == []


def test_valuation_with_stale_fx_weights_and_partial_failures(client, users, settings, monkeypatch):
    alice, _ = users
    add_holding(client, alice, "AAPL", shares=2, avg_cost=100)
    add_holding(client, alice, "7203.T", shares=10, avg_cost=2500)
    add_holding(client, alice, "EURTEST", shares=3, avg_cost=5)
    add_holding(client, alice, "FAILED", shares=5)
    settings.watchlist_summary_concurrency = 2
    active = peak = 0

    async def get_quote(symbol, _db):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.01)
            if symbol == "FAILED":
                raise ExternalAPIError()
            return {
                "AAPL": quote("AAPL", 120, day_change=-2, day_change_pct=-1.64, stale=True,
                              cached_at="2026-11-24T01:00:00Z"),
                "7203.T": quote("7203.T", 2000, "JPY"),
                "EURTEST": quote("EURTEST", 6, "EUR"),
            }[symbol]
        finally:
            active -= 1

    usd_jpy = AsyncMock(return_value={"rate": 150, "stale": True, "cached_at": "2026-11-25T00:00:00Z"})
    monkeypatch.setattr(stock_service, "get_current_price", get_quote)
    monkeypatch.setattr(forex_service, "get_exchange_rate", usd_jpy)
    response = client.get(endpoint(alice, "portfolio"), headers=alice["headers"])
    assert response.status_code == 200, response.text
    data = response.json()
    assert peak == 2 and usd_jpy.await_count == 1
    assert data["partial"] and data["stale"] and data["forex_stale"]
    assert data["cached_at"] == "2026-11-24T01:00:00Z"
    assert data["forex_cached_at"] == "2026-11-25T00:00:00Z"
    assert data["usd_jpy"] == 150
    totals = data["totals"]
    assert totals == pytest.approx({
        "market_value_jpy": 56000, "cost_basis_jpy": 55000, "unrealized_pl_jpy": 1000,
        "unrealized_pl_pct": 1000 / 55000 * 100,
    })
    us, jp, eur, failed = data["holdings"]
    assert (us["name"], us["currency"], us["shares"], us["avg_cost"], us["price"]) == ("AAPL 社", "USD", 2, 100, 120)
    assert us["stale"] and us["cached_at"] == data["cached_at"]
    assert (us["market_value"], us["cost_basis"], us["unrealized_pl"], us["unrealized_pl_pct"]) == (240, 200, 40, 20)
    assert (us["market_value_jpy"], us["cost_basis_jpy"], us["unrealized_pl_jpy"]) == (36000, 30000, 6000)
    assert (us["day_change"], us["day_change_pct"]) == (-2, -1.64)
    assert us["weight_pct"] == pytest.approx(36000 / 56000 * 100)
    assert us["weight_pct"] + jp["weight_pct"] == pytest.approx(100)
    assert jp["unrealized_pl_jpy"] == -5000 and jp["unrealized_pl_pct"] == -20
    assert eur["market_value"] == 18 and eur["market_value_jpy"] is None and eur["fx_unavailable"]
    assert failed["price"] is None and failed["weight_pct"] is None and "株価" in failed["error"]

    usd_jpy.side_effect = ExternalAPIError()
    no_fx = client.get(endpoint(alice, "portfolio"), headers=alice["headers"]).json()
    assert no_fx["partial"] and no_fx["usd_jpy"] is None
    assert no_fx["totals"]["market_value_jpy"] == 20000
    assert no_fx["holdings"][0]["fx_unavailable"]


def test_real_quote_cache_keeps_alert_and_valuation_metadata(client, users, settings, monkeypatch):
    alice, _ = users
    settings.cache_ttl_current_price = 60
    calls = []

    def get_provider_quote(symbol):
        calls.append(symbol)
        return quote(symbol, 120, "JPY", timestamp=utc_iso(), day_change=-10, day_change_pct=-8,
                     market_time="2026-11-26T23:30:00Z", market_date="2026-11-27")

    monkeypatch.setattr(stock_service._client, "get_current_price", get_provider_quote)
    add_holding(client, alice, "AAPL", avg_cost=100)
    add_alert(client, alice, "AAPL", kind="change_pct", threshold=5)
    url = endpoint(alice, "portfolio")
    assert client.get(url, headers=alice["headers"]).json()["holdings"][0]["day_change"] == -10
    cached = client.get(url, headers=alice["headers"]).json()["holdings"][0]
    assert cached["currency"] == "JPY" and cached["name"] == "AAPL 社" and cached["day_change_pct"] == -8
    result = client.post(endpoint(alice, "alerts/check"), headers=alice["headers"]).json()
    assert result["fired"][0]["observed_value"] == 8
    assert result["fired"][0]["last_triggered_market_date"] == "2026-11-27"
    with sqlite3.connect(settings.database_url.split("///", 1)[1]) as db:
        assert db.execute(
            "SELECT quote_market_time, quote_market_date FROM stock_prices WHERE symbol = 'AAPL'"
        ).fetchone() == ("2026-11-26T23:30:00Z", "2026-11-27")
    assert calls == ["AAPL"]


def test_quote_client_returns_utc_market_time_and_exchange_session_date(monkeypatch):
    clock = datetime(2026, 11, 26, 23, 30, tzinfo=UTC)
    info = {"regularMarketPrice": 120, "regularMarketTime": int(clock.timestamp()), "exchangeTimezoneName": "Pacific/Auckland"}
    monkeypatch.setattr(yfinance_client.yf, "Ticker", lambda _: SimpleNamespace(info=info))
    client = yfinance_client.YFinanceClient()
    result = client.get_current_price("nzx.nz")
    assert result["market_time"] == "2026-11-26T23:30:00Z"
    assert result["market_date"] == "2026-11-27"
    del info["regularMarketTime"]
    assert "market_time" not in client.get_current_price("nzx.nz")
    info.clear()
    with pytest.raises(SymbolNotFoundError, match="銘柄が見つかりません"):
        client.get_current_price("ZZZZNOTREAL")


@pytest.mark.parametrize(("section", "fields"), [
    ("portfolio", {"shares": 1, "avg_cost": 100}),
    ("alerts", {"kind": "above", "threshold": 100}),
])
def test_symbol_lookup_on_create_and_when_symbol_changes(client, users, monkeypatch, section, fields):
    alice, bob = users
    path, headers = endpoint(alice, section), alice["headers"]
    failures = {
        "ZZZZNOTREAL": SymbolNotFoundError("ZZZZNOTREAL"), "LIMITED": RateLimitError(),
        "SERVERERROR": ExternalAPIError(), "UNAVAILABLE": UpstreamUnavailableError(),
    }

    async def lookup(symbol, _db):
        if symbol in failures:
            raise failures[symbol]
        return quote(symbol, 120)

    quotes = AsyncMock(side_effect=lookup)
    monkeypatch.setattr(stock_service, "get_current_price", quotes)
    bad = fields | {"symbol": " zzzznotreal "}
    created = client.post(path, headers=headers, json=bad)
    assert created.status_code == 404
    assert created.json()["detail"] == "銘柄が見つかりません: ZZZZNOTREAL"
    for symbol in ("LIMITED", "SERVERERROR", "UNAVAILABLE"):
        assert client.post(path, headers=headers, json=fields | {"symbol": symbol}).status_code == 201

    initial = fields | {"symbol": "KNOWN"}
    row = client.post(path, headers=headers, json=initial).json()
    item = f"{path}/{row['id']}"
    quotes.reset_mock()
    assert client.put(item, headers=headers, json=initial).status_code == 200
    foreign = f"{endpoint(bob, section)}/{row['id']}"
    assert client.put(foreign, headers=bob["headers"], json=bad).status_code == 404
    quotes.assert_not_awaited()
    updated = client.put(item, headers=headers, json=bad)
    assert updated.status_code == 404 and updated.json()["detail"] == created.json()["detail"]
    assert client.put(item, headers=headers, json=initial).json()["symbol"] == "KNOWN"
    if section == "portfolio":
        assert client.put(item, headers=headers, json=fields | {"symbol": "LIMITED"}).status_code == 409
    changed = client.put(item, headers=headers, json=fields | {"symbol": " other "})
    assert changed.status_code == 200 and changed.json()["symbol"] == "OTHER"
    failures["OFFLINE"] = UpstreamUnavailableError()
    changed = client.put(item, headers=headers, json=fields | {"symbol": "OFFLINE"})
    assert changed.status_code == 200 and changed.json()["symbol"] == "OFFLINE"
    assert [call.args[0] for call in quotes.await_args_list] == ["ZZZZNOTREAL", "OTHER", "OFFLINE"]


def test_alert_crud_limits_and_ownership(client, users, settings):
    alice, bob = users
    path, headers = endpoint(alice, "alerts"), alice["headers"]
    settings.price_alert_max_per_user = 1
    assert client.get(path).status_code == 401
    assert client.get(path, headers=bob["headers"]).status_code == 403
    assert client.post(path + "/check", headers=bob["headers"]).status_code == 403
    raw = {"symbol": "msft", "kind": "below", "threshold": 200}
    for invalid in ({"kind": "invalid"}, {"threshold": 0}, {"threshold": -5}, {"symbol": "M/S"}):
        assert client.post(path, headers=headers, json=raw | invalid).status_code == 422
    alert = add_alert(client, alice, " aapl ", note="決算")
    assert alert["symbol"] == "AAPL" and alert["armed"] and alert["last_triggered_at"] is None
    assert client.post(path, headers=headers, json=raw).status_code == 400
    assert client.get(path, headers=headers).json() == {"alerts": [alert]}
    foreign = f"{endpoint(bob, 'alerts')}/{alert['id']}"
    assert client.put(foreign, headers=bob["headers"], json=raw).status_code == 404
    assert client.delete(foreign, headers=bob["headers"]).status_code == 404
    item = f"{path}/{alert['id']}"
    updated = client.put(item, headers=headers, json=raw | {"enabled": False, "note": "停止"}).json()
    assert (updated["symbol"], updated["kind"], updated["enabled"], updated["note"]) == ("MSFT", "below", False, "停止")
    assert client.delete(item, headers=headers).status_code == 204
    assert client.delete(item, headers=headers).status_code == 404
    assert client.get(path, headers=headers).json() == {"alerts": []}


def test_alert_check_cooldown_crossback_stale_errors_and_user_scope(client, users, settings, monkeypatch):
    alice, bob = users
    a = add_alert(client, alice, "TEST", kind="above", threshold=100)
    b = add_alert(client, alice, "TEST", kind="below", threshold=100)
    c = add_alert(client, alice, "TEST", kind="change_pct", threshold=5)
    stale = add_alert(client, alice, "STALE")
    failed = add_alert(client, alice, "FAIL")
    gap = add_alert(client, alice, "NOCHANGE", kind="change_pct")
    add_alert(client, alice, "DISABLED", enabled=False)
    add_alert(client, bob, "BOB")
    now = datetime(2026, 11, 26, tzinfo=UTC).replace(tzinfo=None)
    price = 100
    calls = []

    async def get_quote(symbol, _db):
        calls.append(symbol)
        if symbol == "FAIL":
            raise ExternalAPIError("provider secret")
        if symbol == "STALE":
            return quote(symbol, 500, stale=True, cached_at="2026-11-25T02:00:00Z")
        if symbol == "NOCHANGE":
            return quote(symbol, 500)
        return quote(symbol, price, day_change_pct=-5)

    monkeypatch.setattr(stock_service, "get_current_price", get_quote)
    monkeypatch.setattr(price_alert_service, "to_db", lambda: now)

    def check():
        calls.clear()
        result = client.post(endpoint(alice, "alerts/check"), headers=alice["headers"])
        assert result.status_code == 200, result.text
        assert sorted(calls) == ["FAIL", "NOCHANGE", "STALE", "TEST"]
        return result.json()

    first = check()
    assert [item["id"] for item in first["fired"]] == [a["id"], b["id"], c["id"]]
    assert [item["observed_value"] for item in first["fired"]] == [100, 100, 5]
    assert all(item["trigger_count"] == 1 and item["last_triggered_at"] == utc_iso(now) for item in first["fired"])
    assert [item["id"] for item in first["skipped"]] == [stale["id"], failed["id"], gap["id"]]
    assert first["skipped"][0]["stale"] and first["skipped"][0]["cached_at"] == "2026-11-25T02:00:00Z"
    assert "株価を取得できません" in first["skipped"][1]["reason"]
    assert "騰落率" in first["skipped"][2]["reason"]

    now += timedelta(minutes=settings.alert_cooldown_minutes - 1)
    price = 99
    assert check()["fired"] == []
    price = 100
    assert check()["fired"] == []
    now += timedelta(minutes=1)
    assert [item["id"] for item in check()["fired"]] == [a["id"]]
    now += timedelta(minutes=60)
    assert check()["fired"] == []
    price = 101
    assert check()["fired"] == []
    price = 99
    assert [item["id"] for item in check()["fired"]] == [b["id"]]
    rows = client.get(endpoint(alice, "alerts"), headers=alice["headers"]).json()["alerts"]
    assert [item["trigger_count"] for item in rows] == [2, 2, 1, 0, 0, 0, 0]


def test_daily_change_alert_fires_once_per_market_session_including_weekends(client, users, settings, monkeypatch):
    alice, _ = users
    add_alert(client, alice, "NZX.NZ", kind="change_pct", threshold=5)
    settings.alert_cooldown_minutes = 60
    now = datetime(2026, 11, 26, 23, 30)
    price_quote = quote("NZX.NZ", 120, day_change_pct=-5,
                        market_time="2026-11-26T23:15:00Z", market_date="2026-11-27")
    monkeypatch.setattr(stock_service, "get_current_price", AsyncMock(return_value=price_quote))
    monkeypatch.setattr(price_alert_service, "to_db", lambda: now)
    url = endpoint(alice, "alerts/check")

    def check():
        result = client.post(url, headers=alice["headers"])
        assert result.status_code == 200, result.text
        return result.json()["fired"]

    assert check()[0]["last_triggered_market_date"] == "2026-11-27"
    now += timedelta(hours=2)
    price_quote["market_time"] = "2026-11-27T01:30:00Z"
    assert check() == []
    now += timedelta(days=1)
    assert check() == []
    now += timedelta(days=1)
    assert check() == []
    now += timedelta(days=1)
    price_quote.update(market_time="2026-11-30T01:30:00Z", market_date="2026-11-30")
    fired = check()
    assert fired[0]["trigger_count"] == 2 and fired[0]["last_triggered_market_date"] == "2026-11-30"


@pytest.mark.parametrize("initial_market_time", [None, "2026-11-25T14:30:00Z"])
def test_daily_change_without_market_time_limits_notifications_to_one_per_utc_date(
    client, users, monkeypatch, initial_market_time,
):
    alice, _ = users
    add_alert(client, alice, "TEST", kind="change_pct", threshold=5)
    now = datetime(2026, 11, 26, 22, 30)
    price_quote = quote("TEST", 120, day_change_pct=5, market_time=initial_market_time)
    monkeypatch.setattr(stock_service, "get_current_price", AsyncMock(return_value=price_quote))
    monkeypatch.setattr(price_alert_service, "to_db", lambda: now)

    def check():
        return client.post(endpoint(alice, "alerts/check"), headers=alice["headers"]).json()["fired"]

    expected = "2026-11-25" if initial_market_time else "2026-11-26"
    assert check()[0]["last_triggered_market_date"] == expected
    price_quote["market_time"] = None
    now += timedelta(hours=1)
    assert check() == []
    now += timedelta(minutes=30)
    assert check()[0]["last_triggered_market_date"] == "2026-11-27"


def test_concurrent_alert_checks_record_one_trigger(client, users, monkeypatch):
    alice, _ = users
    add_alert(client, alice, "RACE")
    waiting = 0
    ready = asyncio.Event()

    async def get_quote(symbol, _db):
        nonlocal waiting
        waiting += 1
        if waiting == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), 3)
        return quote(symbol, 120)

    monkeypatch.setattr(stock_service, "get_current_price", get_quote)

    async def check_twice():
        from app.database import AsyncSessionLocal

        async def check():
            async with AsyncSessionLocal() as db:
                return await price_alert_service.check_alerts(alice["id"], db)

        return await asyncio.gather(check(), check())

    results = client.portal.call(check_twice)
    assert waiting == 2
    assert sorted(len(result.fired) for result in results) == [0, 1]
    alerts = client.get(endpoint(alice, "alerts"), headers=alice["headers"]).json()["alerts"]
    assert alerts[0]["trigger_count"] == 1


def test_check_and_valuation_share_the_data_rate_limit(client, users, settings):
    alice, bob = users
    settings.rate_limit_enabled = True
    settings.rate_limit_data_requests = 1
    assert client.post(endpoint(alice, "alerts/check"), headers=alice["headers"]).status_code == 200
    limited = client.get(endpoint(alice, "portfolio"), headers=alice["headers"])
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) > 0
    assert client.get(endpoint(bob, "portfolio"), headers=bob["headers"]).status_code == 200


def test_backup_roundtrip_version_one_and_account_deletion(client, users, settings, monkeypatch):
    alice, bob = users
    add_holding(client, alice, "AAPL", acquired_on="2026-11-25", note="保有")
    add_alert(client, alice, "AAPL", note="高値")
    add_alert(client, alice, "AAPL", kind="change_pct", threshold=5, note="騰落率")
    monkeypatch.setattr(stock_service, "get_current_price", AsyncMock(return_value=quote(
        "AAPL", 120, day_change_pct=10, market_time="2026-11-26T14:30:00Z",
    )))
    client.post(endpoint(alice, "alerts/check"), headers=alice["headers"])
    backup = client.get(endpoint(alice, "export"), headers=alice["headers"]).json()
    assert backup["holdings"][0]["acquired_on"] == "2026-11-25"
    assert len(backup["alerts"]) == 2 and backup["alerts"][0]["trigger_count"] == 1
    assert not backup["alerts"][0]["armed"]
    assert backup["alerts"][1]["last_triggered_market_date"] == "2026-11-26"
    add_holding(client, bob, "OLD")
    add_alert(client, bob, "OLD")

    path, headers = endpoint(bob, "import"), bob["headers"]
    result = client.post(path + "?mode=replace", headers=headers, json=backup).json()
    assert result["imported"]["holdings"] == 1 and result["imported"]["alerts"] == 2
    copied = client.get(endpoint(bob, "export"), headers=headers).json()
    for section in ("holdings", "alerts"):
        assert copied[section] == backup[section]
    merged = client.post(path, headers=headers, json=backup).json()
    assert merged["imported"]["holdings"] == 0 and merged["imported"]["alerts"] == 0
    legacy = {field: value for field, value in backup.items() if field not in ("holdings", "alerts")}
    legacy["version"] = 1
    assert client.post(path + "?mode=replace", headers=headers, json=legacy).status_code == 200
    preserved = client.get(endpoint(bob, "export"), headers=headers).json()
    assert preserved["holdings"] == backup["holdings"] and preserved["alerts"] == backup["alerts"]

    deleted = client.request("DELETE", f"/user/{alice['id']}", headers=alice["headers"],
                             json={"current_password": "password123"})
    assert deleted.status_code == 204
    with sqlite3.connect(settings.database_url.split("///", 1)[1]) as db:
        assert db.execute("SELECT user_id FROM portfolio_holdings").fetchall() == [(bob["id"],)]
        assert db.execute("SELECT user_id FROM price_alerts").fetchall() == [(bob["id"],), (bob["id"],)]
