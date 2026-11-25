from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Request

from app.config import Settings
from app.rate_limit import SlidingWindowLimiter, client_ip

BODY = {"username": "alice", "password": "incorrect"}
DETAIL = "試行回数が多すぎます。しばらくしてから再試行してください。"


@pytest.fixture
def enabled(settings):
    settings.rate_limit_enabled = True
    return settings


def test_sixth_failed_login(client, enabled, monkeypatch):
    login = AsyncMock(side_effect=ValueError("認証失敗"))
    monkeypatch.setattr("app.routers.user.user_service.login_user", login)
    assert [client.post("/user/login", json=BODY).status_code for _ in range(5)] == [401] * 5
    response = client.post("/user/login", json={**BODY, "username": "ALICE"},
                           headers={"X-Forwarded-For": "another-ip"})
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1
    assert response.json()["detail"] == DETAIL
    assert login.await_count == 5


def test_success_resets_username_but_not_ip(client, enabled, monkeypatch):
    user = SimpleNamespace(id=1, username="alice")
    login = AsyncMock(side_effect=[ValueError("failed")] * 4 + [user] + [ValueError("failed")] * 5)
    monkeypatch.setattr("app.routers.user.user_service.login_user", login)
    statuses = [client.post("/user/login", json=BODY).status_code for _ in range(11)]
    assert statuses == [401] * 4 + [200] + [401] * 5 + [429]


@pytest.mark.parametrize("success", [False, True])
def test_ip_cap_across_usernames(client, enabled, monkeypatch, success):
    login = AsyncMock(return_value=SimpleNamespace(id=1, username="alice")) if success else AsyncMock(
        side_effect=ValueError("failed"))
    monkeypatch.setattr("app.routers.user.user_service.login_user", login)
    statuses = [client.post("/user/login", json={**BODY, "username": f"user-{i}"}).status_code for i in range(21)]
    assert statuses == [200 if success else 401] * 20 + [429]
    assert login.await_count == 20


def test_register_cap(client, enabled, monkeypatch):
    register = AsyncMock(return_value=SimpleNamespace(id=1, username="alice"))
    monkeypatch.setattr("app.routers.user.user_service.register_user", register)
    responses = [client.post("/user/register", json={**BODY, "password": "password123"}) for _ in range(6)]
    assert [r.status_code for r in responses] == [200] * 5 + [429]
    assert int(responses[-1].headers["Retry-After"]) >= 1
    assert responses[-1].json()["detail"] == DETAIL
    assert register.await_count == 5


def test_disabled_flag(client, settings, monkeypatch):
    assert not settings.rate_limit_enabled
    monkeypatch.setattr("app.routers.user.user_service.login_user", AsyncMock(side_effect=ValueError("failed")))
    monkeypatch.setattr("app.routers.user.user_service.register_user",
                        AsyncMock(return_value=SimpleNamespace(id=1, username="alice")))
    assert [client.post("/user/login", json=BODY).status_code for _ in range(25)] == [401] * 25
    assert [client.post("/user/register", json=BODY).status_code for _ in range(7)] == [200] * 7


def test_pruning_at_exact_boundary_and_configured_limits(monkeypatch):
    settings = Settings(_env_file=None, rate_limit_login_failures=1, rate_limit_login_ip_attempts=1,
                        rate_limit_register_attempts=1, rate_limit_login_window_seconds=2,
                        rate_limit_login_ip_window_seconds=3, rate_limit_register_window_seconds=4)
    limiter = SlidingWindowLimiter()
    now = [100]
    monkeypatch.setattr("app.rate_limit.time.monotonic", lambda: now[0])
    limiter.begin_login("ip", "Name", settings)
    limiter.finish_login("ip", "Name", settings, success=False)
    limiter.register("ip", settings)
    for action in (lambda: limiter.begin_login("ip", "other", settings), lambda: limiter.register("ip", settings)):
        with pytest.raises(HTTPException) as error:
            action()
        assert error.value.status_code == 429
    now[0] = 102
    limiter.begin_login("other", "new", settings)
    assert ("login-failure", "ip", "name") not in limiter._entries
    assert ("register", "ip") in limiter._entries
    now[0] = 104
    limiter.register("new-ip", settings)
    assert set(limiter._entries) == {("login-ip", "other"), ("register", "new-ip")}
    now[0] = 108
    limiter.register("last-ip", settings)
    assert set(limiter._entries) == {("register", "last-ip")}
    limiter.reset()
    assert not limiter._entries


def test_no_request_client():
    assert client_ip(Request({"type": "http", "client": None, "headers": []})) == "unknown"


def test_concurrent_ip_admission():
    limiter = SlidingWindowLimiter()
    settings = Settings(_env_file=None)

    def attempt(number):
        try:
            limiter.begin_login("same-ip", str(number), settings)
            return True
        except HTTPException as error:
            assert error.status_code == 429
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(60))) == 20
