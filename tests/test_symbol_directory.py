import asyncio
import zipfile
from datetime import timedelta
from io import BytesIO
from unittest.mock import Mock
from xml.sax.saxutils import escape

import httpx
import pytest
import yfinance
from sqlalchemy import select, text

from app import database, main
from app.clients import symbol_lists
from app.clients.symbol_lists import (
    JPX_HEADERS,
    JPX_PAGE,
    JPX_XLSX,
    NASDAQ_LISTED,
    OTHER_LISTED,
    SymbolListError,
    discover_jpx_link,
    parse_jpx,
    parse_nasdaq,
)
from app.models.db_models import CompanyProfile, SymbolDirectoryEntry
from app.schemas.symbols import SymbolMatch
from app.services import symbol_directory as service
from app.timeutil import to_db

JPX_SAMPLE = [
    ("9432", "ＮＴＴ", "プライム（内国株式）", "情報・通信業"),
    ("130A", "ＮＴＴ関連ＥＴＦ", "ETF・ETN", "-"),
    ("7203", "トヨタ自動車", "プライム（内国株式）", "輸送用機器"),
    ("25935", "優先株式", "プライム（内国株式）", "-"),
]
NASDAQ = (
    b"Symbol|Security Name|Test Issue|ETF\n"
    b"AAPL|Apple Inc. Common Stock |N|N\n"
    b"NVDA|NVIDIA Corporation Common Stock|N|N\n"
    b"TEST|Test Company|Y|N\n"
    b"ACQW|Some Acquisition - Warrants|N|N\n"
    b"File Creation Time: 0101202600:00|||\n"
)
OTHER = (
    b"ACT Symbol|Security Name|Exchange|Test Issue|ETF\n"
    b"BRK.B|Berkshire Hathaway Inc. - Class B Common Stock|N|N|N\n"
    b"ABC$A|Example Preferred Stock|A|N|N\n"
    b"XYZ|XYZ Fund|P|N|Y\n"
    b"TEST|Test Stock|Z|Y|N\n"
    b"EDGE|Example |Z|N|N\n"
    b"IEXF|Example Growth ETF|V|N|Y\n"
    b"ABC|Example Corp. Common Stock|A|N|N\n"
    b"File Creation Time: 0101202600:00||||\n"
)


def xlsx(entries=JPX_SAMPLE):
    shared = list(JPX_HEADERS)
    rows = ['<row r="1">' + "".join(
        f'<c r="{column}1" t="s"><v>{index}</v></c>' for index, column in enumerate("ABCDEFGHIJ")
    ) + "</row>"]
    for index, (code, name, market, sector) in enumerate(entries, 2):
        code_cell = (f"<v>{code}</v>" if code.isdecimal() else f"<is><t>{code}</t></is>")
        code_type = ' t="inlineStr"' if not code.isdecimal() else ""
        rows.append(
            f'<row r="{index}"><c r="A{index}"><v>20260101</v></c>'
            f'<c r="B{index}"{code_type}>{code_cell}</c>'
            f'<c r="C{index}" t="s"><v>{len(shared)}</v></c>'
            f'<c r="D{index}" t="inlineStr"><is><t>{escape(market)}</t></is></c>'
            f'<c r="F{index}" t="inlineStr"><is><t>{escape(sector)}</t></is></c></row>'
        )
        shared.append(name)
    ns = symbol_lists.NS[1:-1]
    content = "".join(f"<si><t>{escape(value)}</t></si>" for value in shared)
    out = BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as book:
        book.writestr("xl/sharedStrings.xml", f'<sst xmlns="{ns}">{content}</sst>')
        book.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{ns}"><sheetData>{"".join(rows)}</sheetData></worksheet>')
    return out.getvalue()


def test_jpx_shared_inline_and_numeric_cells():
    ntt, etf, toyota, preferred = parse_jpx(xlsx())
    assert ntt == SymbolMatch("9432.T", "ＮＴＴ", "JP", "プライム", "情報・通信業", False, "jpx")
    assert etf == SymbolMatch("130A.T", "ＮＴＴ関連ＥＴＦ", "JP", "ETF・ETN", None, True, "jpx")
    assert toyota.symbol == "7203.T"
    assert preferred.symbol == "25935.T"
    with pytest.raises(SymbolListError, match="銘柄行"):
        parse_jpx(xlsx([JPX_SAMPLE[0], JPX_SAMPLE[0]]))
    with pytest.raises(SymbolListError, match="読み取れません"):
        parse_jpx(b"not a zip")


def test_jpx_size_limits(monkeypatch):
    data = xlsx()
    with monkeypatch.context() as patch:
        patch.setattr(symbol_lists, "MAX_DOWNLOAD_BYTES", len(data) - 1)
        with pytest.raises(SymbolListError, match="ダウンロードサイズ"):
            parse_jpx(data)
    monkeypatch.setattr(symbol_lists, "MAX_MEMBER_BYTES", 50)
    with pytest.raises(SymbolListError, match="展開サイズ"):
        parse_jpx(data)


def test_jpx_link_discovery():
    new = '/markets/statistics-equities/misc/new-dir/data_j.xlsx'
    old = 'https://www.jpx.co.jp/markets/statistics-equities/misc/old-dir/data_j.xls'
    assert discover_jpx_link(f"<a href='{old}'>上場銘柄一覧</a>") == old
    assert discover_jpx_link(f"<a href='{old}'></a><a href=\"{new}?v=2\"></a>") == f"https://www.jpx.co.jp{new}"
    assert discover_jpx_link('<a href="./new-dir/data_j.xlsx"></a>') == f"https://www.jpx.co.jp{new}"
    assert discover_jpx_link(f'<a href="https://evil.invalid{new}"></a>') is None


def test_nasdaq_listing_and_other_exchanges():
    apple, nvidia = parse_nasdaq(NASDAQ)
    assert apple == SymbolMatch("AAPL", "Apple Inc.", "US", "NASDAQ", None, False, "nasdaqtrader")
    assert nvidia.name == "NVIDIA Corporation"
    berkshire, fund, cboe, iex, american = parse_nasdaq(OTHER, other=True)
    assert (berkshire.symbol, berkshire.name, berkshire.segment) == ("BRK-B", "Berkshire Hathaway Inc. (Class B)", "NYSE")
    assert (fund.segment, cboe.segment, iex.segment, american.segment) == ("NYSE Arca", "Cboe BZX", "IEX", "NYSE American")
    assert fund.is_etf and iex.is_etf and not cboe.is_etf
    with pytest.raises(SymbolListError, match="作成日時"):
        parse_nasdaq(NASDAQ.split(b"File Creation Time")[0])


def test_http_download_limit(monkeypatch):
    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"xxxx"
            yield b"xxxx"

    async def scenario():
        monkeypatch.setattr(symbol_lists, "MAX_DOWNLOAD_BYTES", 7)
        transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=Chunks()))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(SymbolListError, match="ダウンロードサイズ"):
                await symbol_lists.download(client, JPX_XLSX)

    asyncio.run(scenario())


def test_refresh_replaces_each_market_and_rolls_back_on_failure(async_db, monkeypatch):
    engine, sessions = async_db
    directory = service.SymbolDirectory()
    monkeypatch.setattr(service, "directory", directory)
    jpx_file = "https://www.jpx.co.jp/markets/statistics-equities/misc/new-dir/data_j.xlsx"
    payloads = {JPX_PAGE: b"no matching link", JPX_XLSX: xlsx(), NASDAQ_LISTED: NASDAQ, OTHER_LISTED: OTHER}
    requests = []

    def handle(request):
        assert request.headers["User-Agent"] == "tera-quant-lab/1.0"
        url = str(request.url)
        requests.append(url)
        return httpx.Response(200, content=payloads[url])

    async def symbols():
        async with sessions() as db:
            return set(await db.scalars(select(SymbolDirectoryEntry.symbol)))

    async def scenario():
        await database.upgrade_db()
        transport = httpx.MockTransport(handle)
        assert await service.refresh_symbol_directory(transport=transport) == {"JP": 4, "US": 7}
        assert requests == [JPX_PAGE, JPX_XLSX, NASDAQ_LISTED, OTHER_LISTED]
        assert len(await symbols()) == 11
        payloads[JPX_PAGE] = f'<a href="{jpx_file}">'.encode()
        payloads[jpx_file] = xlsx(JPX_SAMPLE[1:2])
        payloads[OTHER_LISTED] = b"bad provider response"
        assert await service.refresh_symbol_directory(transport=transport) == {"JP": 1}
        assert jpx_file in requests
        assert await symbols() == {"130A.T", "AAPL", "NVDA", "BRK-B", "XYZ", "IEXF", "EDGE", "ABC"}
        assert not directory.index.search("7203")
        assert directory.index.search("130a")[0].symbol == "130A.T"

        async with engine.begin() as connection:
            await connection.execute(text("""
                CREATE TRIGGER reject_symbol BEFORE INSERT ON symbol_directory
                WHEN NEW.symbol = '7203.T' BEGIN SELECT RAISE(ABORT, 'reject new row'); END
            """))
        before = await symbols()
        payloads[jpx_file] = xlsx(JPX_SAMPLE)
        assert await service.refresh_symbol_directory(["JP"], transport=transport) == {}
        assert await symbols() == before
        payloads[jpx_file] = b"malformed"
        assert await service.refresh_symbol_directory(["JP"], transport=transport) == {}
        assert await symbols() == before
        await engine.dispose()

    asyncio.run(scenario())


def test_normalization_and_ranking():
    assert {service.normalize_name(value) for value in ("ＮＴＴ", "ntt", "NTT", "Ｎ・ＴＴ（株）", "㈱ ＮＴＴ")} == {"ntt"}
    assert service.normalize_name("とよた株式会社") == service.normalize_name("トヨタ") == "トヨタ"
    assert service.normalize_name("ﾄﾖﾀ") == "トヨタ"
    assert service.normalize_name("Alpha Holdings Inc.,") == "alphaholdings"
    assert service.normalize_name("トヨタホールディングス") == "トヨタホールディングス"
    assert service.normalize_name("Toyota Motor Co., Ltd.") == "toyotamotor"
    additional = parse_jpx(xlsx([
        ("9613", "ＮＴＴデータグループ", "プライム", "情報・通信業"),
        ("3116", "トヨタ紡織", "プライム", "輸送用機器"),
        ("1000", "ＮＴＴ通信", "スタンダード", "-"),
        ("1001", "ＮＴＴ通信", "プライム", "-"),
        ("1002", "ＮＴＴ通信", "ETF・ETN", "-"),
    ]))
    index = service.SymbolIndex((row, None) for row in parse_jpx(xlsx()) + parse_nasdaq(NASDAQ) + additional)
    assert [row.symbol for row in index.search("NTT")][:4] == ["9432.T", "1001.T", "9613.T", "1000.T"]
    assert index.search("7203")[0].symbol == "7203.T"
    assert index.search("9432.t")[0].symbol == "9432.T"
    assert [row.symbol for row in index.search("トヨタ")][:2] == ["7203.T", "3116.T"]
    assert index.search("apple")[0].symbol == "AAPL"
    assert index.search("aple")[0].symbol == "AAPL"
    assert index.search("aapl")[0].symbol == "AAPL"
    assert not index.search("unrelated")


def test_latin_names_preserve_word_boundaries():
    rows = parse_jpx(xlsx([
        ("9432", "ＮＴＴ", "プライム", "-"),
        ("8956", "ＮＴＴ都市開発リート投資法人", "REIT", "-"),
        ("9999", "不動産投資法人ＮＴＴ", "REIT", "-"),
    ])) + parse_nasdaq(NASDAQ) + [
        SymbolMatch(symbol, name, "US", "NYSE", None, symbol == "AAPX", "nasdaqtrader")
        for symbol, name in (
            ("A", "Agilent Technologies"),
            ("ALGT", "Allegiant Travel Company"),
            ("BSRT", "BSR Real Estate Investment Trust"),
            ("NTEST", "NT Technologies"),
            ("APLE", "Apple Hospitality REIT"),
            ("AAPX", "T-Rex 2X Long Apple Daily Target ETF"),
            ("MLP", "Maui Land & Pineapple Company"),
            ("PTIV", "Pineapple Financial"),
        )
    ]
    index = service.SymbolIndex((row, service.normalize_name(row.name)) for row in rows)
    assert [row.symbol for row in index.search("NTT")] == ["9432.T", "8956.T", "9999.T"]
    assert [row.symbol for row in index.search("apple")] == ["AAPL", "APLE", "AAPX"]
    assert [row.symbol for row in index.search("rex 2x long apple")] == ["AAPX"]
    assert [row.symbol for row in index.search("apple  hospitality")] == ["APLE"]
    pineapple = SymbolMatch("PINE", "Pineapple", "US", "NYSE", None, False, "nasdaqtrader")
    assert not service.SymbolIndex([(pineapple, None)]).search("apple")


@pytest.mark.parametrize(("symbol", "name"), [
    ("APLE", "Example Holdings"),
    ("APLEX", "Example Holdings"),
    ("EX", "Aple Holdings"),
    ("EX", "The Aple Fund"),
])
def test_fuzzy_only_when_strong_tiers_are_empty(symbol, name):
    apple = SymbolMatch("AAPL", "Apple Inc.", "US", "NASDAQ", None, False, "nasdaqtrader")
    strong = SymbolMatch(symbol, name, "US", "NYSE", None, False, "nasdaqtrader")
    assert service.SymbolIndex([(apple, None)]).search("aple") == [apple]
    assert service.SymbolIndex([(apple, None), (strong, None)]).search("aple") == [strong]


def test_cached_english_profile_search(async_db, monkeypatch):
    engine, sessions = async_db
    directory = service.SymbolDirectory()
    monkeypatch.setattr(service, "directory", directory)

    async def scenario():
        await database.upgrade_db()
        async with sessions.begin() as db:
            db.add(SymbolDirectoryEntry(symbol="7203.T", name="トヨタ自動車", search_key="トヨタ自動車", market="JP",
                                        segment="プライム", sector="輸送用機器", is_etf=False, source="jpx", updated_at=to_db()))
            db.add(CompanyProfile(symbol="7203.T", name="Toyota Motor Corporation", sector="Consumer Cyclical"))
        await directory.load()
        rows = directory.index.search("toyota")
        assert rows == [SymbolMatch("7203.T", "Toyota Motor Corporation", "JP", "プライム", "輸送用機器", False, "profile")]
        assert directory.index.search("7203")[0].source == "jpx"
        await engine.dispose()

    asyncio.run(scenario())


def test_endpoint_fallback_validation_and_failure(client, monkeypatch):
    from app.routers import symbols

    directory = service.SymbolDirectory()
    directory.index = service.SymbolIndex((row, None) for row in parse_jpx(xlsx()) + parse_jpx(xlsx([
        ("9613", "ＮＴＴデータグループ", "プライム", "-"),
    ])))
    monkeypatch.setattr(symbols, "directory", directory)
    calls = []

    def yahoo(query, **kwargs):
        calls.append((query, kwargs))
        if query == "ZZZZ":
            raise RuntimeError("provider down")
        return type("Search", (), {"quotes": [
            {"symbol": "9432.T", "shortname": "NTT", "quoteType": "EQUITY"},
            {"symbol": "AAPL", "shortname": "Apple Inc.", "exchange": "NMS", "quoteType": "EQUITY"},
        ]})()

    monkeypatch.setattr(yfinance, "Search", yahoo)
    for params in ({}, {"q": "   "}, {"q": " " + "a" * 51}, {"q": "x", "limit": 0}, {"q": "x", "limit": 21}):
        assert client.get("/symbols/search", params=params).status_code == 422
    response = client.get("/symbols/search", params={"q": " ＮＴＴ ", "limit": 1})
    assert response.status_code == 200
    assert response.json()["items"][0]["symbol"] == "9432.T"
    assert calls == []
    for query in (" aapl ", "AAPL"):
        rows = client.get("/symbols/search", params={"q": query}).json()["items"]
        assert (rows[0]["symbol"], rows[0]["source"], rows[0]["segment"]) == ("AAPL", "yahoo", "NASDAQ")
    assert len(calls) == 1 and calls[0][1]["max_results"] == 20
    response = client.get("/symbols/search", params={"q": "トヨタ"})
    assert response.status_code == 200
    assert [row["symbol"] for row in response.json()["items"]] == ["7203.T"]
    assert len(calls) == 1
    response = client.get("/symbols/search", params={"q": "zzzz"})
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert [query.casefold() for query, _ in calls] == ["aapl", "zzzz"]


@pytest.mark.parametrize("query", ["トヨタ", "とよた", "ﾄﾖﾀ", "任天堂", "三菱UFJ", "𠮷"])
def test_japanese_query_skips_yahoo_even_without_local_matches(monkeypatch, query):
    yahoo = Mock(return_value=[])
    monkeypatch.setattr(service._yahoo, "search_symbols", yahoo)
    assert asyncio.run(service.SymbolDirectory().search(query, 5)) == []
    yahoo.assert_not_called()


@pytest.mark.parametrize(("query", "symbol"), [
    ("7203", "7203.T"), ("ﾄﾖﾀ", "7203.T"), ("toyota", "7203.T"),
    ("ＡＡＰＬ", "AAPL"), ("apple", "AAPL"), ("rex", "AAPX"),
])
def test_strong_local_match_skips_yahoo(monkeypatch, query, symbol):
    directory = service.SymbolDirectory()
    directory.index = service.SymbolIndex((row, None) for row in [
        *parse_jpx(xlsx()), *parse_nasdaq(NASDAQ),
        SymbolMatch("AAPX", "T-Rex 2X Long Apple Daily Target ETF", "US", "Cboe BZX", None, True, "nasdaqtrader"),
    ])
    directory.index.remember_profile("7203.T", "Toyota Motor Corporation", None)
    yahoo = Mock(return_value=[])
    monkeypatch.setattr(service._yahoo, "search_symbols", yahoo)
    assert asyncio.run(directory.search(query, 1))[0].symbol == symbol
    yahoo.assert_not_called()


def test_yahoo_only_returns_relevant_matches(monkeypatch):
    yahoo = Mock(return_value=[
        {"symbol": "EMIS", "shortname": "Emmis Acquisition Corp.", "quoteType": "EQUITY"},
        {"symbol": "MLP", "shortname": "Maui Land & Pineapple Company", "quoteType": "EQUITY"},
        {"symbol": "APPLEF", "shortname": "Example Fund", "quoteType": "ETF"},
        {"symbol": "AAPX", "shortname": "T-Rex 2X Long Apple Daily Target ETF", "quoteType": "ETF"},
    ])
    monkeypatch.setattr(service._yahoo, "search_symbols", yahoo)
    directory = service.SymbolDirectory()
    assert asyncio.run(directory.search("eMAXIS", 5)) == []
    assert [row.symbol for row in asyncio.run(directory.search("apple", 5))] == ["APPLEF", "AAPX"]
    assert [call.args for call in yahoo.call_args_list] == [("eMAXIS",), ("apple",)]


@pytest.mark.parametrize(("count", "calls"), [(2, 1), (3, 0)])
def test_yahoo_fallback_threshold_for_ticker_prefix_only(monkeypatch, count, calls):
    rows = [
        SymbolMatch(symbol, "Berkshire Hathaway", "US", "NYSE", None, False, "nasdaqtrader")
        for symbol in ("BRK-A", "BRK-B", "BRK-C")[:count]
    ]
    directory = service.SymbolDirectory()
    directory.index = service.SymbolIndex((row, None) for row in rows)
    yahoo = Mock(return_value=[])
    monkeypatch.setattr(service._yahoo, "search_symbols", yahoo)
    assert asyncio.run(directory.search("brk", 1)) == rows[:1]
    assert yahoo.call_count == calls


def test_empty_directory_shares_fallback_with_unmatched_profiles(monkeypatch):
    directory = service.SymbolDirectory()
    for symbol, name in (("7203.T", "Toyota Motor"), ("3116.T", "Toyota Boshoku"), ("6201.T", "Toyota Industries")):
        directory.index.remember_profile(symbol, name, None)
    calls = []

    def yahoo(query, **kwargs):
        calls.append(query)
        return type("Search", (), {"quotes": [
            {"symbol": "TM", "shortname": "Toyota Motor", "exchange": "NYQ", "quoteType": "EQUITY"},
        ]})()

    monkeypatch.setattr(yfinance, "Search", yahoo)

    async def scenario():
        profiles = await directory.search("toyota", 10)
        assert {row.symbol for row in profiles} == {"7203.T", "3116.T", "6201.T"}
        assert calls == []
        first, second = await asyncio.gather(directory.search("TM", 10), directory.search("tm", 10))
        assert first == second
        assert [row.symbol for row in first] == ["TM"]
        assert calls == ["TM"]

    asyncio.run(scenario())


@pytest.mark.parametrize(("enabled", "age", "expected"), [
    (False, 9, []), (True, 3, []), (True, 9, ["JP"]), (True, None, ["JP", "US"]),
])
def test_startup_refresh_age_and_disabled(settings, monkeypatch, enabled, age, expected):
    settings.symbol_directory_enabled = enabled
    settings.symbol_directory_max_age_days = 5
    calls = []

    async def load():
        return {} if age is None else {"JP": to_db() - timedelta(days=age), "US": to_db()}

    async def refresh(markets):
        calls.extend(markets)
        return {}

    monkeypatch.setattr(service.directory, "load", load)
    monkeypatch.setattr(service, "refresh_symbol_directory", refresh)
    asyncio.run(service.initialize_symbol_directory(settings))
    assert calls == expected


def test_startup_serves_requests_while_refresh_is_pending(async_db, monkeypatch, settings):
    settings.symbol_directory_enabled = True
    monkeypatch.setattr(main, "settings", settings)

    async def scenario():
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def refresh(markets):
            assert markets == ["JP", "US"]
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        monkeypatch.setattr(service, "refresh_symbol_directory", refresh)
        async with asyncio.timeout(5), main.lifespan(main.app):
            await started.wait()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                assert (await client.get("/health")).status_code == 200
            assert not stopped.is_set()
        assert stopped.is_set()

    asyncio.run(scenario())
