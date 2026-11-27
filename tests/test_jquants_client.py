import asyncio
import logging
from datetime import date

import httpx
import pytest
from pydantic import SecretStr

from app.clients.jquants_client import JQuantsAuthError, JQuantsClient, JQuantsError, RequestLimiter, parse_summary
from app.middleware import TokenRedactingFormatter

KEY = "example-private-jquants-key"
DAY = date(2026, 9, 1)


class Clock:
    def __init__(self):
        self.now = 0.0
        self.delays = []

    def clock(self):
        return self.now

    async def sleep(self, delay):
        self.delays.append(delay)
        self.now += delay


@pytest.fixture
def mock_api(settings):
    settings.jquants_api_key = SecretStr(KEY)
    settings.jquants_base_url = "https://jquants.invalid/v2"
    clock = Clock()
    limiter = RequestLimiter(clock=clock.clock, sleep=clock.sleep)

    def client(handler):
        return JQuantsClient(settings, transport=httpx.MockTransport(handler), limiter=limiter, sleep=clock.sleep)

    return client, clock


def test_client_headers_pagination_and_summary_parsing(mock_api):
    client, _ = mock_api
    requests = []

    def handle(req):
        assert req.headers["x-api-key"] == KEY and req.headers["user-agent"] == "tera-quant-lab"
        requests.append((req.url.path, dict(req.url.params)))
        if req.url.path.endswith("/master"):
            return httpx.Response(200, json={"data": []})
        match req.url.params.get("pagination_key"):
            case None:
                return httpx.Response(200, json={"data": [{
                    "Code": "186A0", "DiscNo": "001", "DiscDate": DAY.isoformat(), "DiscTime": "15:30:00",
                    "DocType": "FYFinancialStatements_NonConsolidated_JP", "CurPerType": "FY", "Sales": "",
                    "NCSales": "1200", "NP": "", "EPS": "12.5", "ShOutFY": "123456789012345678", "NxFNp": "20",
                    "EqAR": "0.112", "CurFYEn": "2026-03-31",
                }], "pagination_key": "page-2"})
            case "page-2":
                return httpx.Response(200, json={"data": [], "cursor": "page-3"})
            case "page-3":
                return httpx.Response(200, json={"data": []})
            case _:
                pytest.fail("unexpected page")

    async def scenario():
        async with client(handle) as api:
            assert await api.master() == []
            raw, = await api.summaries(DAY)
        row = parse_summary(raw)
        assert row["disc_no"] == "001" and row["symbol"] == "186A.T"
        assert row["sales"] == 1200 and row["net_profit"] is None and row["eps"] == 12.5
        assert row["next_forecast_net_profit"] == 20 and row["equity_ratio"] == 0.112
        assert row["shares_outstanding_fy"] == 123456789012345678
        assert row["period_type"] == "FY" and row["doc_type"].startswith("FYFinancialStatements")
        assert row["consolidated"] is False and row["fy_end"] == date(2026, 3, 31)
        assert requests == [
            ("/v2/equities/master", {}), ("/v2/fins/summary", {"date": DAY.isoformat()}),
            ("/v2/fins/summary", {"date": DAY.isoformat(), "pagination_key": "page-2"}),
            ("/v2/fins/summary", {"date": DAY.isoformat(), "pagination_key": "page-3"}),
        ]

    asyncio.run(scenario())


def test_retry_429_waits_two_minutes_and_is_rate_limited(mock_api, settings):
    client, clock = mock_api
    settings.jquants_requests_per_minute = 1
    attempts = []

    def handle(_):
        attempts.append(clock.now)
        if len(attempts) == 1:
            return httpx.Response(429)
        if len(attempts) == 2:
            return httpx.Response(500)
        return httpx.Response(200, json={"data": []})

    async def scenario():
        async with client(handle) as api:
            assert await api.summaries(DAY) == []

    asyncio.run(scenario())
    assert attempts == pytest.approx([0, 120, 180.001])
    assert clock.delays == pytest.approx([120, 2, 58.001])


def test_limiter_is_shared_between_clients_and_serializes_every_request(mock_api, settings):
    client, clock = mock_api
    settings.jquants_requests_per_minute = 2
    attempts = []
    in_flight = False

    async def handle(request):
        nonlocal in_flight
        assert not in_flight
        in_flight = True
        attempts.append(clock.now)
        await asyncio.sleep(0)
        in_flight = False
        payload = {"data": []}
        if "pagination_key" not in request.url.params:
            payload["pagination_key"] = "second"
        return httpx.Response(200, json=payload)

    async def scenario():
        async with client(handle) as first, client(handle) as second:
            await asyncio.gather(*(api.summaries(DAY) for api in (first, second, first, second)))

    asyncio.run(scenario())
    assert len(attempts) == 8
    assert all(sum(at <= latest <= at + 60 for latest in attempts) <= 2 for at in attempts)


def test_errors_are_bounded_and_never_contain_key(mock_api, settings, caplog):
    client, clock = mock_api
    attempts = []

    def network_error(request):
        attempts.append(request)
        raise httpx.ConnectError(KEY, request=request)

    async def scenario():
        for code, error in ((403, JQuantsAuthError), (400, JQuantsError)):
            async with client(lambda _, code=code: httpx.Response(code, json={"message": KEY})) as api:
                with pytest.raises(error, match=str(code)) as failure:
                    await api.summaries(DAY)
                assert KEY not in repr(failure.value)
        async with client(network_error) as api:
            with pytest.raises(JQuantsError, match="接続できません") as failure:
                await api.summaries(DAY)
            assert failure.value.__context__ is None
        async with client(lambda _: httpx.Response(200, json={"data": [{"DocType": KEY}]})) as api:
            with pytest.raises(JQuantsError, match="認証情報"):
                await api.summaries(DAY)

    async def echo_cursor():
        async with client(lambda _: httpx.Response(200, json={"data": [], "cursor": KEY})) as api:
            with pytest.raises(JQuantsError, match="ページ情報"):
                await api.summaries(DAY)

    with caplog.at_level(logging.DEBUG):
        asyncio.run(scenario())
        asyncio.run(echo_cursor())
    assert len(attempts) == 3 and clock.delays[:2] == [1, 2]
    assert KEY not in caplog.text and KEY not in repr(settings)
    log = logging.LogRecord("test", logging.INFO, "", 0, "key=%s", (KEY,), None)
    assert TokenRedactingFormatter().format(log) == "key=[非表示]"
