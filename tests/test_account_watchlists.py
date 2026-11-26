import asyncio
import csv
import io
import os
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import timedelta
from unittest.mock import AsyncMock

import jwt
from sqlalchemy import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app import database
from app.exceptions import UpstreamUnavailableError
from app.models.db_models import Indicator
from app.services import indicator_service, profile_service, stock_service, user_service
from app.timeutil import utc_iso, utcnow


def prefix(user):
    return f"/user/{user['id']}"


def list_path(user):
    return f"{prefix(user)}/lists/{user['list_id']}"


def headers(user):
    return {"Authorization": f"Bearer {user['access_token']}"}


def assert_unauthorized(response):
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_password_change_and_logout_all_revoke_tokens(client, users, settings):
    alice, bob = users
    now = utcnow()
    legacy = jwt.encode(
        {"sub": str(alice["id"]), "iat": now, "exp": now + timedelta(minutes=10)},
        os.environ["SECRET_KEY"], algorithm="HS256",
    )
    legacy_headers = headers({"access_token": legacy})
    lists = prefix(alice) + "/lists"
    assert client.get(lists, headers=legacy_headers).status_code == 200
    path = prefix(alice) + "/password"
    policy = client.post(path, headers=legacy_headers, json={
        "current_password": "password123", "new_password": "short",
    })
    assert policy.status_code == 400
    assert policy.json()["detail"] == "パスワードは8文字以上にしてください"
    response = client.post(path, headers=legacy_headers, json={
        "current_password": "password123", "new_password": "replacement-password",
    })
    assert response.status_code == 200
    refreshed = response.json()
    assert set(refreshed) == {"id", "username", "token_type", "access_token"}
    assert jwt.decode(refreshed["access_token"], os.environ["SECRET_KEY"], algorithms=["HS256"])["ver"] == 1
    assert client.get(lists, headers=headers(refreshed)).status_code == 200
    assert_unauthorized(client.get(lists, headers=legacy_headers))
    assert_unauthorized(client.get(lists, headers=alice["headers"]))
    with closing(sqlite3.connect(make_url(settings.database_url).database)) as db:
        stored_hash, version = db.execute("SELECT password_hash, token_version FROM users WHERE id=?", (alice["id"],)).fetchone()
    assert version == 1
    assert stored_hash.startswith("$argon2") and user_service.ph.verify(stored_hash, "replacement-password")
    assert client.post("/user/login", json={"username": "alice", "password": "password123"}).status_code == 401
    credentials = {"username": "alice", "password": "replacement-password"}
    login = client.post("/user/login", json=credentials)
    assert login.status_code == 200
    assert client.get(lists, headers=headers(login.json())).status_code == 200
    logout = client.post(prefix(alice) + "/logout-all", headers=headers(refreshed))
    assert logout.status_code == 204 and not logout.content
    assert_unauthorized(client.get(lists, headers=headers(login.json())))
    assert_unauthorized(client.get(lists, headers=headers(refreshed)))
    later = client.post("/user/login", json=credentials)
    assert client.get(lists, headers=headers(later.json())).status_code == 200
    assert client.get(prefix(bob) + "/lists", headers=bob["headers"]).status_code == 200


def test_current_password_failures_share_login_budget(client, users, settings):
    settings.rate_limit_enabled = True
    settings.rate_limit_login_failures = 2
    alice, _ = users
    path = prefix(alice)
    wrong = {"current_password": "incorrect"}
    change = client.post(path + "/password", headers=alice["headers"], json={**wrong, "new_password": "replacement"})
    delete = client.request("DELETE", path, headers=alice["headers"], json=wrong)
    assert change.status_code == delete.status_code == 400
    assert change.json() == delete.json() == {"detail": "現在のパスワードが正しくありません"}
    blocked = client.post("/user/login", json={"username": "alice", "password": "password123"})
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0
    assert client.request("DELETE", path, headers=alice["headers"], json=wrong).status_code == 429
    assert client.post(path + "/password", headers=alice["headers"],
                       json={"current_password": "password123", "new_password": "replacement"}).status_code == 429


def add_item(client, user, symbol, tags, path=None):
    response = client.post((path or list_path(user)) + "/items", headers=user["headers"], json={"symbol": symbol, "tags": tags})
    assert response.status_code == 200


def put_memo(client, user, symbol, memo):
    response = client.put(prefix(user) + f"/memo/{symbol}", headers=user["headers"], json={"memo": memo})
    assert response.status_code == 200


def test_delete_account_removes_owned_rows_and_never_reuses_token_subject(client, users, settings):
    alice, bob = users
    for user in (alice, bob):
        add_item(client, user, "AAPL", [user["username"]])
        put_memo(client, user, "AAPL", user["username"])
    extra = client.post(prefix(bob) + "/lists", headers=bob["headers"], json={"name": "Another"}).json()
    add_item(client, bob, "MSFT", ["private"], f"{prefix(bob)}/lists/{extra['id']}")
    response = client.request("DELETE", prefix(bob), headers=bob["headers"], json={"current_password": "password123"})
    assert response.status_code == 204 and not response.content
    with closing(sqlite3.connect(make_url(settings.database_url).database)) as db:
        assert db.execute("PRAGMA foreign_keys").fetchone() == (0,)
        for table in ("users", "user_lists", "user_list_items", "stock_memos"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone() == (1,)
        assert db.execute("SELECT tags FROM user_list_items").fetchone() == ('["alice"]',)
        assert db.execute("SELECT memo FROM stock_memos").fetchone() == ("alice",)
    assert client.get(list_path(alice), headers=alice["headers"]).status_code == 200
    assert_unauthorized(client.get(prefix(bob) + "/lists", headers=bob["headers"]))
    recreated = client.post("/user/register", json={"username": "bob", "password": "password123"}).json()
    assert recreated["id"] > bob["id"]
    assert_unauthorized(client.get(prefix(bob) + "/lists", headers=bob["headers"]))


def test_summary_caps_symbols_and_charges_once(client, users, settings, monkeypatch):
    alice, bob = users
    route = list_path(alice) + "/summary"
    assert client.get(route, headers=alice["headers"]).json()["items"] == []
    assert client.get(list_path(bob) + "/summary", headers=alice["headers"]).status_code == 403
    alien = f"{prefix(alice)}/lists/{bob['list_id']}/summary"
    assert client.get(alien, headers=alice["headers"]).status_code == 404
    for symbol in ("AAPL", "BAD", "PARTIAL", "MSFT", "^N225"):
        add_item(client, alice, symbol, ["tag"])
    put_memo(client, alice, "BAD", "retain memo")
    settings.watchlist_summary_concurrency = 2
    settings.rate_limit_enabled = True
    settings.rate_limit_data_requests = 1
    active = Counter()
    peak = 0

    def fake(values, *, profile=False):
        async def slow(symbol, db):
            nonlocal peak
            active[symbol] += 1
            peak = max(peak, len(active))
            try:
                await asyncio.sleep(0.02)
                if symbol == "BAD" or (symbol == "PARTIAL" and profile):
                    raise UpstreamUnavailableError("upstream details")
                return values
            finally:
                active[symbol] -= 1
                if not active[symbol]:
                    del active[symbol]
        return slow

    monkeypatch.setattr(stock_service, "get_current_price", fake({"price": 120}))
    monkeypatch.setattr(profile_service, "get_profile", fake({
        "name": "Company", "currency": "USD", "sector": "Tech", "country": "US", "market_cap": 1000,
    }, profile=True))
    monkeypatch.setattr(indicator_service, "get_indicators", fake({"mix_index": 8, "dividend_yield": 2, "per": 4, "pbr": 2}))
    response = client.get(route, headers=alice["headers"])
    assert response.status_code == 200 and peak == 2 and not active
    data = response.json()
    assert data["id"] == alice["list_id"] and data["name"] == "マイリスト"
    complete, error, partial, *_ = data["items"]
    assert complete == {
        "symbol": "AAPL", "tags": ["tag"], "name": "Company", "country": "US", "sector": "Tech",
        "price": 120, "currency": "USD", "market_cap": 1000, "mix_index": 8, "dividend_yield": 2,
        "per": 4, "pbr": 2, "memo": None, "error": None,
    }
    assert error["symbol"] == "BAD" and error["memo"] == "retain memo"
    assert error["error"] == "銘柄データを取得できませんでした。"
    assert partial["name"] is None and partial["price"] == 120
    assert partial["error"] == "一部のデータを取得できませんでした。"
    assert client.get(route, headers=alice["headers"]).status_code == 429
    assert client.get(list_path(bob) + "/summary", headers=bob["headers"]).status_code == 200


def test_cached_negative_pbr_has_no_mix_index_in_detail_or_summary(client, users, settings, monkeypatch):
    alice, _ = users
    add_item(client, alice, "HPQ", [])
    monkeypatch.setattr(stock_service, "get_current_price", AsyncMock(return_value={"price": 20}))
    monkeypatch.setattr(profile_service, "get_profile", AsyncMock(return_value={"name": "HP"}))

    async def seed_indicators():
        async with database.AsyncSessionLocal() as db:
            db.add(Indicator(symbol="HPQ", per=10.67, pbr=-305.39, mix_index=-3257.86))
            await db.commit()

    client.portal.call(seed_indicators)
    detail = client.get("/stock/HPQ/indicators")
    summary = client.get(list_path(alice) + "/summary", headers=alice["headers"])
    assert detail.status_code == summary.status_code == 200
    item, = summary.json()["items"]
    assert item["symbol"] == "HPQ" and item["error"] is None
    for data in (detail.json(), item):
        assert data["mix_index"] is None
        assert data["per"] == 10.67 and data["pbr"] == -305.39
    with closing(sqlite3.connect(make_url(settings.database_url).database)) as db:
        assert db.execute("SELECT mix_index FROM indicators WHERE symbol = 'HPQ'").fetchone() == (-3257.86,)


def test_watchlist_names_and_protected_default(client, users):
    alice, bob = users
    root = prefix(alice) + "/lists"
    auth = alice["headers"]
    assert client.delete(list_path(alice), headers=auth).status_code == 400
    created = client.post(root, headers=auth, json={"name": "  Growth　"})
    assert created.status_code == 200 and created.json()["name"] == "Growth"
    path = root + f"/{created.json()['id']}"
    for name, status in [("", 400), ("　 ", 400), ("株" * 51, 400), (" マイリスト ", 409)]:
        assert client.post(root, headers=auth, json={"name": name}).status_code == status
        assert client.put(path, headers=auth, json={"name": name}).status_code == status
    assert client.post(root, headers=auth, json={"name": "Growth"}).status_code == 409
    assert client.post(prefix(bob) + "/lists", headers=bob["headers"], json={"name": "Growth"}).status_code == 200
    assert client.put(path, headers=auth, json={"name": " Growth "}).status_code == 200
    assert client.put(path, headers=auth, json={"name": " 株 "}).json()["name"] == "株"
    assert client.get(root, headers=auth).json()["default_list_id"] == alice["list_id"]
    assert client.delete(list_path(alice), headers=auth).status_code == 400
    assert client.delete(path, headers=auth).status_code == 200


def exported(client, user):
    response = client.get(prefix(user) + "/export", headers=user["headers"])
    assert response.status_code == 200
    assert response.headers["content-disposition"] == f'attachment; filename="watchlists-{utcnow().date()}.json"'
    return response.json()


def test_backup_replace_round_trip(client, users):
    alice, bob = users
    root = prefix(alice) + "/lists"
    another = client.post(root, headers=alice["headers"], json={"name": "配当株"}).json()
    add_item(client, alice, "AAPL", ["分析", "長期"])
    add_item(client, alice, "aapl", ["高配当"], f"{root}/{another['id']}")
    put_memo(client, alice, "AAPL", "メモ")
    put_memo(client, alice, "TSLA", None)
    add_item(client, bob, "MSFT", ["obsolete"])
    put_memo(client, bob, "MSFT", "obsolete")
    backup = exported(client, alice)
    assert set(backup) == {"version", "exported_at", "username", "lists", "memos", "holdings", "alerts", "backtest_runs"}
    assert backup["version"] == 3 and backup["username"] == "alice"
    assert backup["exported_at"].endswith("Z")
    assert backup["lists"][0]["items"][0]["added_at"].endswith("Z")
    imported = client.post(prefix(bob) + "/import?mode=replace", headers=bob["headers"], json=backup)
    assert imported.status_code == 200
    assert imported.json() == {
        "imported": {"lists": 2, "items": 2, "tags": 3, "memos": 2, "holdings": 0, "alerts": 0, "backtest_runs": 0}, "skipped": [],
    }
    copy = exported(client, bob)
    assert copy["username"] == "bob"
    for key in ("lists", "memos"):
        assert copy[key] == backup[key] == exported(client, alice)[key]


def test_backup_merge_and_skipped_symbols(client, users):
    alice, _ = users
    add_item(client, alice, "AAPL", ["keep"])
    put_memo(client, alice, "AAPL", "keep")
    put_memo(client, alice, "MSFT", "")
    backup = exported(client, alice)
    backup["lists"][0]["items"] = [
        {"symbol": " aapl ", "tags": ["keep", "new"], "added_at": None},
        {"symbol": " msft ", "tags": [], "added_at": None},
        {"symbol": "A/B", "tags": [], "added_at": None},
    ]
    backup["lists"].append({"name": "  ", "items": []})
    backup["memos"] = [
        {"symbol": symbol, "memo": "source", "updated_at": utc_iso()}
        for symbol in ("AAPL", " msft ", "M/S")
    ]
    path = prefix(alice) + "/import"
    response = client.post(path, headers=alice["headers"], json=backup)
    assert response.status_code == 200
    assert response.json()["imported"] == {"lists": 0, "items": 1, "tags": 1, "memos": 1, "holdings": 0, "alerts": 0, "backtest_runs": 0}
    assert [entry["entry"] for entry in response.json()["skipped"]] == ["マイリスト / A/B", "  ", "メモ / M/S"]
    merged = exported(client, alice)
    assert [item["tags"] for item in merged["lists"][0]["items"]] == [["keep", "new"], []]
    assert [memo["memo"] for memo in merged["memos"]] == ["keep", "source"]
    assert client.post(path, headers=alice["headers"], json=backup).json()["imported"] == dict.fromkeys(
        ("lists", "items", "tags", "memos", "holdings", "alerts", "backtest_runs"), 0,
    )


def test_backup_size_and_schema(client, users, settings):
    alice, _ = users
    path = prefix(alice) + "/import"
    backup = exported(client, alice)
    for payload in (b"not-json", b"{}", b"[]"):
        response = client.post(path, headers=alice["headers"], content=payload)
        assert response.status_code == 422
        assert response.json()["detail"][0]["msg"] == "バックアップの形式が正しくありません"
    for invalid in (dict(backup, version="1"), dict(backup, extra=True), dict(backup, version=4)):
        assert client.post(path, headers=alice["headers"], json=invalid).status_code == 422
    settings.user_import_max_bytes = 100
    response = client.post(path, headers=alice["headers"], content=iter([b"x" * 50, b"x" * 51]))
    assert response.status_code == 413 and "100 バイト以内" in response.json()["detail"]


def test_backup_replace_rolls_back_mid_import_failure(client, users, monkeypatch):
    alice, bob = users
    add_item(client, alice, "AAPL", ["keep"])
    put_memo(client, alice, "AAPL", "keep")
    add_item(client, bob, "MSFT", [])
    original = exported(client, alice)
    source = exported(client, bob)

    async def fail_after_flush(session):
        await session.flush()
        raise RuntimeError("injected before commit")

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "commit", fail_after_flush)
        failure = client.post(prefix(alice) + "/import?mode=replace", headers=alice["headers"], json=source)
        assert failure.status_code == 500
    restored = exported(client, alice)
    assert restored["lists"] == original["lists"]
    assert restored["memos"] == original["memos"]


def test_csv_quotes_fields_and_neutralizes_formulas(client, users, settings):
    alice, bob = users
    add_item(client, alice, "AAPL", ["+SUM(1,2)", 'quotes"'])
    add_item(client, alice, "MSFT", [])
    put_memo(client, alice, "AAPL", 'line 1, "quoted"\nline 2')
    put_memo(client, alice, "MSFT", " \t@SUM(1)")
    put_memo(client, bob, "AAPL", "private data")
    with closing(sqlite3.connect(make_url(settings.database_url).database)) as db, db:
        db.execute("INSERT INTO company_profiles (symbol, name, cached_at) VALUES (?, ?, ?)", ("AAPL", "=NAME()", utc_iso()))
    response = client.get(list_path(alice) + "/export.csv", headers=alice["headers"])
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"].startswith("attachment;")
    assert list(csv.reader(io.StringIO(response.content.decode("utf-8-sig")))) == [
        ["symbol", "name", "tags", "memo"],
        ["AAPL", "\t=NAME()", '\t+SUM(1,2), quotes"', 'line 1, "quoted"\nline 2'],
        ["MSFT", "", "", "\t \t@SUM(1)"],
    ]
