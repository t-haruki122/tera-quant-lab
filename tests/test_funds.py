import asyncio
from datetime import date, datetime, timedelta
from unittest.mock import Mock

import httpx
import pytest
from sqlalchemy import delete

from app import database
from app.clients import fund_client
from app.fund_catalog import FUND_SOURCE
from app.models.db_models import FundNAV, StockPrice
from app.services import fund_service, symbol_directory
from app.timeutil import to_db
from app.validation import normalize_symbol
from tests.test_api_tokens import create_token
from tests.test_mcp_http import session
from tests.test_watchlist_portfolio import add_item, list_path

CODE = "0331418A"
SYMBOL = f"FUND-{CODE}"


def sample():
    return {
        "result": {"status": 200, "retcount": 1, "errcd": None, "errmsg": None, "function": "外部公開用最新ファンド情報（指定ファンド）"},
        "errors": {"count": 0, "error_list": None},
        "datasets": [{
            "fund_cd": "253425", "isin_cd": "JP90C000H1T1", "association_fund_cd": CODE, "base_date": "20261127",
            "fund_name": "ｅＭＡＸＩＳ Ｓｌｉｍ 全世界株式（オール・カントリー）", "nav": 35610, "cmp_prev_day": "246",
            "percentage_change": "0.7", "cancellation_price": 35610, "netassets": 13649778246815,
            "netassets_change_cmp_prev_day": "0.79", "percentage_change_1m": "-4.89", "percentage_change_3m": "-7.82",
            "percentage_change_6m": "-5.22", "percentage_change_1y": "9.36", "percentage_change_3y": "70.31",
            "percentage_change_full": "256.1", "percentage_change_max_1m": "0.1", "percentage_change_max_3m": "3.14",
            "percentage_change_max_6m": "18.22", "percentage_change_max_1y": "56.14", "percentage_change_max_3y": "113.03",
            "percentage_change_max_full": "290.19", "percentage_change_min_1m": "-6.04", "percentage_change_min_3m": "-8.4",
            "percentage_change_min_6m": "-5.39", "percentage_change_min_1y": "9.23", "percentage_change_min_3y": "30",
            "percentage_change_min_full": "-18.98", "risk_1y": "13.61", "risk_3y": "15.87", "risk_full": "18.49",
            "risk_return_1y": "0.71", "risk_return_3y": "1.26", "risk_return_full": "0.95", "nav_max_1m": "37902",
            "nav_max_1m_dt": "20261029", "nav_max_3m": "38905", "nav_max_3m_dt": "20260831", "nav_max_6m": "39019",
            "nav_max_6m_dt": "20260814", "nav_max_1y": "39019", "nav_max_1y_dt": "20260814", "nav_max_3y": "39019",
            "nav_max_3y_dt": "20260814", "nav_max_full": "39019", "nav_max_full_dt": "20260814", "nav_min_1m": "35364",
            "nav_min_1m_dt": "20261126", "nav_min_3m": "35364", "nav_min_3m_dt": "20261126", "nav_min_6m": "35364",
            "nav_min_6m_dt": "20261126", "nav_min_1y": "32176", "nav_min_1y_dt": "20260331", "nav_min_3y": "20165",
            "nav_min_3y_dt": "20231208", "nav_min_full": "8102", "nav_min_full_dt": "20200324",
        }],
    }


def response(code=CODE, **updates):
    payload = sample()
    payload["datasets"][0].update(association_fund_cd=code, **updates)
    return httpx.Response(200, json=payload)


def mock_provider(monkeypatch, handler):
    original = fund_client.fetch_latest
    requests = []

    def handle(request):
        code = request.url.path.rsplit("/", 1)[-1]
        requests.append(code)
        assert request.method == "GET"
        assert str(request.url) == f"{fund_client.LATEST_URL}/{code}"
        assert request.headers["User-Agent"] == "tera-quant-lab/1.0"
        assert "authorization" not in request.headers
        return handler(code)

    transport = httpx.MockTransport(handle)

    async def fetch(code):
        return await original(code, transport=transport)

    monkeypatch.setattr(fund_client, "fetch_latest", fetch)
    return requests


def test_parse_verified_mixed_types_and_strict_symbol():
    data = fund_client.parse_latest(CODE, sample())
    assert data.fund_name == "eMAXIS Slim 全世界株式(オール・カントリー)"
    assert data.base_date == date(2026, 11, 27)
    assert (data.nav, data.cancellation_price, data.net_assets, data.change, data.change_pct) == (
        35610, 35610, 13649778246815, 246, 0.7,
    )
    assert (data.fund_cd, data.isin) == ("253425", "JP90C000H1T1")
    assert data.stats["returns"] == {"1m": -4.89, "3m": -7.82, "6m": -5.22, "1y": 9.36, "3y": 70.31, "full": 256.1}
    assert data.stats["risk"]["1y"] == 13.61
    assert data.stats["risk_return"]["3y"] == 1.26
    assert data.stats["max_nav"]["1m"] == {"nav": 37902, "date": "2026-10-29"}
    assert data.stats["min_nav"]["full"] == {"nav": 8102, "date": "2020-03-24"}
    assert normalize_symbol(" fund-0331418a ") == SYMBOL
    for invalid in ("FUND-0331418", "FUND-0331418AJ", "FUND-0331.18A", "FUND-０331418A", "FUND-0331/18A"):
        with pytest.raises(ValueError):
            normalize_symbol(invalid)


@pytest.mark.parametrize("http_status", [404, 200])
def test_unsupported_fund_client_and_endpoints(client, users, monkeypatch, http_status):
    mock = mock_provider(monkeypatch, lambda _: httpx.Response(http_status, json={"result": {"status": 200}, "datasets": []}))
    user = users[0]
    for route in (f"/stock/{SYMBOL}/fund", f"/stock/{SYMBOL}/snapshot", f"/symbols/search?q={CODE}"):
        result = client.get(route)
        assert result.status_code == 404
        assert result.json()["detail"] == "このファンドは未対応です（三菱UFJアセットマネジメントのファンドのみ対応）"
    result = client.post(f"{list_path(user)}/items", headers=user["headers"], json={"symbol": SYMBOL})
    assert result.status_code == 404
    assert mock == [CODE]


def test_fund_refresh_coalescing_history_and_stale_cache(client, monkeypatch):
    now = datetime(2026, 11, 27, 10)
    monkeypatch.setattr("app.cache.to_db", lambda: now)
    monkeypatch.setattr(fund_service, "to_db", lambda: now)
    observed_day, nav, failed = "20261127", 35610, False

    def upstream(code):
        if failed:
            raise httpx.ReadTimeout("mock timeout")
        return response(code, base_date=observed_day, nav=nav)

    requests = mock_provider(monkeypatch, upstream)
    path = f"/stock/{SYMBOL}"

    async def concurrent_lookup():
        async with database.AsyncSessionLocal() as db:
            return await asyncio.gather(*(fund_service.get_fund(SYMBOL, db) for _ in range(6)))

    assert all(row["nav"] == nav for row in client.portal.call(concurrent_lookup))
    first = client.get(path + "/fund").json()
    assert first["history"] == [{"base_date": "2026-11-27", "nav": 35610}]
    assert first["source_credit"] == FUND_SOURCE
    assert client.get(path + "/snapshot").json()["price"] == 35610
    assert requests == [CODE]

    now += timedelta(hours=6, seconds=1)
    nav = 35611
    assert client.get(path + "/fund").json()["history"] == [{"base_date": "2026-11-27", "nav": 35611}]
    now += timedelta(days=3)
    observed_day, nav = "20261130", 36000
    assert client.get(path + "/fund").json()["history"] == [
        {"base_date": "2026-11-27", "nav": 35611}, {"base_date": "2026-11-30", "nav": 36000},
    ]
    now += timedelta(hours=6, seconds=1)
    failed = True
    stale, cached = [client.get(path + "/fund").json() for _ in range(2)]
    assert stale == cached
    assert stale["nav"] == 36000 and stale["stale"]
    assert stale["cached_at"] == (now - timedelta(hours=6, seconds=1)).isoformat() + "Z"
    assert requests == [CODE] * 4


def test_search_aliases_us_etfs_and_association_code(client, monkeypatch):
    yahoo = Mock(return_value=[{"symbol": "VOO", "quoteType": "ETF", "shortname": "Vanguard S&P 500 ETF"}])
    monkeypatch.setattr(symbol_directory._yahoo, "search_symbols", yahoo)
    requests = mock_provider(monkeypatch, lambda code: response(code, nav=30655, fund_name="ｅＭＡＸＩＳ Ｓｌｉｍ 国内株式（ＴＯＰＩＸ）"))

    def search(query):
        result = client.get("/symbols/search", params={"q": query})
        assert result.status_code == 200, result.text
        return result.json()["items"]

    assert search("オルカン")[0]["symbol"] == SYMBOL
    assert len(search("ｅＭＡＸＩＳ Ｓｌｉｍ")) == 3
    assert {row["symbol"] for row in search("S&P500")} >= {"FUND-03311187", "VOO"}
    assert search("０３３１７１７２") == [{
        "symbol": "FUND-03317172", "name": "eMAXIS Slim 国内株式(TOPIX)", "market": "JP",
        "segment": "投資信託", "sector": None, "is_etf": False, "source": "fund",
    }]
    assert requests == ["03317172"]


def test_cached_portfolio_funds_currency_exposure_and_backup(client, users, monkeypatch):
    alice, bob = users
    path, headers = list_path(alice), alice["headers"]
    navs = {CODE: 35610, "03311187": 25000, "03317172": 30655, "12345678": 40000}
    requests = mock_provider(monkeypatch, lambda code: response(code, nav=navs[code]))
    for code, units in zip(navs, (15000, 20000, 10000, 5000), strict=True):
        add_item(client, alice, "FUND-" + code, units)
    add_item(client, alice, "AAPL", 2)
    assert client.put(path + "/cash", headers=headers, json={"cash_jpy": 1000}).status_code == 200

    async def seed():
        async with database.AsyncSessionLocal.begin() as db:
            db.add_all([
                StockPrice(symbol="AAPL", interval="quote", timestamp=to_db(), close=30, quote_currency="USD"),
                StockPrice(symbol="USDJPY=X", interval="forex", timestamp=to_db(), close=150),
            ])

    client.portal.call(seed)

    def summary():
        result = client.get(path + "/summary?cached=true", headers=headers)
        assert result.status_code == 200, result.text
        return result.json()

    data = summary()
    fund = data["items"][0]
    assert (fund["asset_class"], fund["currency"], fund["unit"], fund["shares"], fund["price"], fund["value_jpy"]) == (
        "投資信託", "JPY", "10,000口あたり", 15000, 35610, 53415,
    )
    assert not fund["pending"] and fund["roe"] is None
    portfolio = data["portfolio"]
    assert portfolio["total_value_jpy"] == 164070
    assert portfolio["concentration"]["holding_count"] == 5
    assert portfolio["concentration"]["largest"]["symbol"] == SYMBOL
    assert portfolio["funds_value_jpy"] == 154070

    def amounts(group):
        return {row["label"]: row["value_jpy"] for row in portfolio[group]}

    assert amounts("by_asset_class") == {"個別株": 9000, "投資信託": 154070, "現金": 1000}
    assert amounts("by_region") == {"日本株": 30655, "米国株": 59000, "その他": 0, "現金": 1000,
                                    "全世界株": 53415, "投資信託（その他）": 20000}
    assert amounts("by_currency") == {"JPY": 31655, "USD": 59000, "外貨（複数）": 53415, "不明": 20000}
    assert amounts("by_sector") == {"不明": 9000}
    assert sum(row["pct"] for row in portfolio["by_asset_class"]) == pytest.approx(100)

    backup = client.get(f"/user/{alice['id']}/export", headers=headers).json()
    result = client.post(f"/user/{bob['id']}/import?mode=replace", headers=bob["headers"], json=backup)
    assert result.status_code == 200 and result.json()["skipped"] == []
    copy = client.get(f"/user/{bob['id']}/export", headers=bob["headers"]).json()
    assert copy["lists"] == backup["lists"]

    async def expire_prices():
        async with database.AsyncSessionLocal.begin() as db:
            await db.execute(delete(StockPrice).where(StockPrice.symbol == "USDJPY=X"))
            await db.execute(delete(FundNAV).where(FundNAV.association_code == "03317172"))

    client.portal.call(expire_prices)
    data = summary()
    assert data["items"][1]["value_jpy"] == 50000
    assert data["items"][2]["pending"] and data["items"][2]["value_jpy"] is None
    assert data["portfolio"]["total_value_jpy"] == 124415
    assert {row["symbol"] for row in data["portfolio"]["excluded"]} == {"AAPL", "FUND-03317172"}
    assert requests == list(navs)


def test_in_app_mcp_returns_fund_detail_search_and_portfolio(client, users, monkeypatch):
    alice = users[0]
    requests = mock_provider(monkeypatch, response)
    add_item(client, alice, SYMBOL, 10000)
    token = create_token(client, alice)["token"]

    async def scenario():
        async with session(token) as mcp:
            result = await mcp.call_tool("get_stock", {"symbol": SYMBOL.lower()})
            assert not result.is_error, result
            assert result.structured_content["nav"] == 35610
            assert result.structured_content["association_code"] == CODE
            result = await mcp.call_tool("search_symbols", {"query": "オルカン"})
            assert not result.is_error, result
            assert result.structured_content["items"][0]["symbol"] == SYMBOL
            result = await mcp.call_tool("get_watchlist", {})
            assert not result.is_error, result
            data = result.structured_content
            fund = data["items"][0]
            assert (fund["shares"], fund["price"], fund["value_jpy"]) == (10000, 35610, 35610)
            assert data["portfolio"]["by_asset_class"][1] == {"label": "投資信託", "value_jpy": 35610, "pct": 100}

    client.portal.call(scenario)
    assert requests == [CODE]
