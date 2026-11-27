import asyncio
import logging
from datetime import date, timedelta
from functools import partial

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app import database
from app import main as app_main
from app.clients.jquants_client import JQuantsClient, RequestLimiter, parse_summary
from app.models.db_models import JQFinSummary, JQIssue
from app.services import jquants_sync as jq
from tests.test_api_tokens import bearer, create_token
from tests.test_jquants_client import KEY
from tests.test_mcp_http import session

TODAY = date(2026, 11, 27)
START = date(2026, 8, 31)
END = date(2026, 9, 4)
MASTER = [
    {"Date": END.isoformat(), "Code": "72030", "CoName": "トヨタ自動車", "S33": "3700", "Mkt": "0111"},
    {"Date": END.isoformat(), "Code": "72035", "CoName": "優先株式"},
]


def disclosure(disc_no, day, **fields):
    return {"DiscNo": disc_no, "Code": "72030", "DiscDate": day.isoformat(),
            "DocType": "FYFinancialStatements_Consolidated_JP", "CurPerType": "FY", **fields}


@pytest.fixture
def configured(settings):
    settings.jquants_api_key = SecretStr(KEY)
    settings.jquants_base_url = "https://jquants.invalid/v2"
    settings.jquants_requests_per_minute = 999
    return settings


@pytest.fixture
def short_window(monkeypatch):
    clock = [TODAY]
    monkeypatch.setattr(jq, "today_jst", lambda: clock[0])
    monkeypatch.setattr(jq, "_plan_window", lambda settings, today: (START, END + (today - TODAY)))
    return clock


def use_transport(monkeypatch, handler):
    monkeypatch.setattr(jq, "JQuantsClient", partial(
        JQuantsClient, transport=httpx.MockTransport(handler), limiter=RequestLimiter(),
    ))


def test_free_window_uses_two_calendar_years_and_twelve_weeks(settings):
    assert jq._plan_window(settings, TODAY) == (date(2024, 9, 4), END)
    assert list(jq._days(END, END + timedelta(days=3))) == [END, END + timedelta(days=3)]


def test_oldest_first_resume_daily_refresh_and_disc_no_correction(configured, async_db, short_window, monkeypatch):
    engine, sessions = async_db
    fetched = []
    masters = 0
    interrupt = True
    correction = False

    def handle(request):
        nonlocal masters
        if request.url.path.endswith("/master"):
            assert not request.url.params
            masters += 1
            return httpx.Response(200, json={"data": MASTER})
        day = date.fromisoformat(request.url.params["date"])
        fetched.append(day)
        if interrupt and day > START:
            raise asyncio.CancelledError
        if day == START:
            fields = {"Sales": "200", "NP": ""} if correction else {"Sales": "100", "NP": "12"}
            return httpx.Response(200, json={"data": [disclosure("001", day, **fields)]})
        return httpx.Response(200, json={"data": []})

    use_transport(monkeypatch, handle)

    async def scenario():
        nonlocal interrupt, correction
        await database.upgrade_db()
        with pytest.raises(asyncio.CancelledError):
            await jq.sync_jquants(configured)
        assert fetched == [START, START + timedelta(days=1)]
        async with sessions() as db:
            status = await jq.sync_status(db, configured)
            assert status["last_synced_date"] == START.isoformat()
            assert status["progress"] == {"synced_days": 1, "total_days": 5}
            assert not status["backfill_complete"]
        interrupt = False
        fetched.clear()
        assert await jq.sync_jquants(configured) is jq.SyncResult.COMPLETE
        assert fetched == [START + timedelta(days=n) for n in range(1, 5)]
        correction = True
        assert await jq.sync_jquants(configured, dates_from=START, dates_to=START) is jq.SyncResult.COMPLETE
        assert fetched[-1] == START and masters == 1
        async with sessions() as db:
            summary, = list(await db.scalars(select(JQFinSummary)))
            assert summary.sales == 200 and summary.net_profit is None and summary.disc_no == "001"
            issues = list(await db.scalars(select(JQIssue).order_by(JQIssue.code5)))
            assert [issue.symbol for issue in issues] == ["7203.T", None]
            assert issues[0].sector33_code == "3700" and issues[0].market_code == "0111"
            status = await jq.sync_status(db, configured)
            assert status["backfill_complete"] and status["last_synced_date"] == END.isoformat()
            assert status["issues_count"] == 2 and status["summaries_count"] == 1
        fetched.clear()
        short_window[0] += timedelta(days=3)
        assert await jq.sync_jquants(configured) is jq.SyncResult.COMPLETE
        assert fetched == [END + timedelta(days=3)] and masters == 2
        await engine.dispose()

    asyncio.run(scenario())


def test_explicit_window_rejection_adjusts_edges_but_auth_failure_stops(
    configured, async_db, short_window, monkeypatch, caplog,
):
    engine, sessions = async_db
    fetched = []
    blocked = True

    def handle(request):
        if request.url.path.endswith("/master"):
            return httpx.Response(200, json={"data": []})
        day = date.fromisoformat(request.url.params["date"])
        fetched.append(day)
        if day == START:
            return httpx.Response(400, json={"message": "The requested date is outside the available range"})
        if day == END and blocked:
            return httpx.Response(403, json={"message": "The requested date must be within the data range"})
        if day == END + timedelta(days=3):
            return httpx.Response(403, json={"message": f"Invalid key {KEY}"})
        return httpx.Response(200, json={"data": []})

    use_transport(monkeypatch, handle)

    async def scenario():
        nonlocal blocked
        await database.upgrade_db()
        assert await jq.sync_jquants(configured) is jq.SyncResult.COMPLETE
        async with sessions() as db:
            status = await jq.sync_status(db, configured)
            assert status["window"] == {"start": START + timedelta(days=1), "end": END - timedelta(days=1)}
            assert status["backfill_complete"] and status["last_rejected_date"] == END.isoformat()
        calls = fetched.copy()
        assert await jq.sync_jquants(configured) is jq.SyncResult.COMPLETE
        assert fetched == calls
        blocked = False
        short_window[0] += timedelta(days=5)
        assert await jq.sync_jquants(configured) is jq.SyncResult.FAILED
        assert fetched[len(calls):] == [END, END + timedelta(days=3)]
        async with sessions() as db:
            status = await jq.sync_status(db, configured)
            assert status["last_synced_date"] == END.isoformat()
            assert "403" in status["last_error"] and KEY not in str(status)
        await engine.dispose()

    with caplog.at_level(logging.WARNING):
        asyncio.run(scenario())
    assert "403" in caplog.text and KEY not in caplog.text


def test_process_guard_and_sqlite_lease_block_concurrent_sync(configured, async_db, short_window, monkeypatch):
    engine, _ = async_db

    async def scenario():
        await database.upgrade_db()
        requested = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def handle(request):
            calls.append(request.url.path)
            requested.set()
            await release.wait()
            return httpx.Response(200, json={"data": []})

        use_transport(monkeypatch, handle)
        first = asyncio.create_task(jq.sync_jquants(configured))
        try:
            await asyncio.wait_for(requested.wait(), 3)
            assert await jq.sync_jquants(configured) is jq.SyncResult.BUSY
            with monkeypatch.context() as worker:
                worker.setattr(jq, "_PROCESS_LOCK", asyncio.Lock())
                assert await jq.sync_jquants(configured) is jq.SyncResult.BUSY
            assert calls == ["/v2/equities/master"]
        finally:
            release.set()
            assert await first is jq.SyncResult.COMPLETE
            await engine.dispose()

    asyncio.run(scenario())


def test_explicit_rejection_in_middle_does_not_strand_later_dates(configured, async_db, short_window, monkeypatch):
    engine, sessions = async_db
    short_window[0] += timedelta(days=14)
    rejected = START + timedelta(days=2)
    fetched = []

    def handle(request):
        if request.url.path.endswith("/summary"):
            day = date.fromisoformat(request.url.params["date"])
            fetched.append(day)
            if day == rejected:
                return httpx.Response(403, json={"message": "Data not available for the specified date"})
        return httpx.Response(200, json={"data": []})

    use_transport(monkeypatch, handle)

    async def scenario():
        await database.upgrade_db()
        assert await jq.sync_jquants(configured) is jq.SyncResult.COMPLETE
        assert await jq.sync_jquants(configured) is jq.SyncResult.COMPLETE
        assert fetched == list(jq._days(START, END + timedelta(days=14)))
        async with sessions() as db:
            status = await jq.sync_status(db, configured)
            assert status["backfill_complete"] and status["last_rejected_date"] == rejected.isoformat()
        await engine.dispose()

    asyncio.run(scenario())


def test_without_key_app_starts_and_does_not_request_jquants(request, caplog, monkeypatch, settings):
    def unexpected(*args, **kwargs):
        pytest.fail("J-Quants client was instantiated without a key")

    monkeypatch.setattr(jq, "JQuantsClient", unexpected)
    with caplog.at_level(logging.INFO):
        client = request.getfixturevalue("client")
    assert asyncio.run(jq.sync_jquants(settings)) is jq.SyncResult.DISABLED
    assert client.get("/health").status_code == 200
    status = client.get("/jquants/status").json()
    assert status["configured"] is status["enabled"] is False
    assert status["issues_count"] == status["summaries_count"] == 0 and status["window"] is None
    assert "jquants" not in client.get("/stats").json()
    assert len([row for row in caplog.records if "J-Quants の API キーが未設定です" in row.message]) == 1


def test_startup_yields_while_real_sync_waits_for_master(configured, request, monkeypatch):
    async def blocked(request):
        assert request.url.path.endswith("/master")
        await asyncio.Event().wait()

    use_transport(monkeypatch, blocked)
    client = request.getfixturevalue("client")
    assert client.get("/health").status_code == 200
    assert client.get("/jquants/status").json()["enabled"] is True


def test_public_read_api_api_token_stats_and_http_mcp(configured, short_window, monkeypatch, request):
    async def idle(_):
        await asyncio.Event().wait()

    monkeypatch.setattr(app_main, "sync_jquants_periodically", idle)
    client = request.getfixturevalue("client")
    alice = request.getfixturevalue("users")[0]
    token = create_token(client, alice)["token"]
    current = disclosure("003", END, DiscTime="15:00:00", CurFYSt="2025-04-01", CurPerEn="2026-03-31",
                         Sales="1000", OP="125", NP="18", Eq="200", TA="500", ROE="0.09")
    previous = disclosure("001", START, Eq="100", CurPerEn="2025-03-31")
    quarterly = disclosure("004", END, DiscTime="16:00:00", CurPerType="1Q", Sales="0", EqAR="0.42")

    async def seed():
        async with database.AsyncSessionLocal.begin() as db:
            for row in (current, previous, quarterly):
                db.add(JQFinSummary(**parse_summary(row), fetched_at=jq.to_db()))
            await jq._update_state(db, {"last_synced_date": END.isoformat(), "last_error": f"外部エラー {KEY}"})

    client.portal.call(seed)
    path = "/stock/7203.t/jq-financials"
    public = client.get(path).json()
    assert public == client.get(path, headers=bearer(token)).json()
    assert public["delay_weeks"] == 12 and public["currency"] == "JPY"
    one_q, fy, prior = public["summaries"]
    assert [row["disc_no"] for row in (one_q, fy, prior)] == ["004", "003", "001"]
    assert one_q["roe_calc"] is one_q["operating_margin"] is None and one_q["equity_ratio"] == 0.42
    assert fy["roe_calc"] == pytest.approx(0.12) and fy["operating_margin"] == 0.125 and fy["equity_ratio"] == 0.4
    for symbol in ("AAPL", "9999.T"):
        response = client.get(f"/stock/{symbol}/jq-financials")
        assert response.status_code == 200 and response.json()["summaries"] == [] and response.json()["note"]
    checked = client.get("/jquants/status", headers={**bearer(token), "X-Request-ID": KEY})
    assert KEY not in checked.headers["X-Request-ID"]
    status = checked.json()
    assert status["backfill_complete"] and status["summaries_count"] == 3
    assert KEY not in str(status) and KEY not in client.get("/stats").text and KEY not in str(public)
    for url in (path, "/jquants/status"):
        assert client.get(url, headers=bearer("tql_invalid")).status_code == 401

    async def mcp_check():
        async with session(token) as mcp:
            response = await mcp.call_tool("get_jp_financials", {"symbol": "7203.T"})
            assert not response.is_error and response.structured_content == public
            assert "12 週間遅れ" in response.structured_content["delay_note"]

    client.portal.call(mcp_check)
