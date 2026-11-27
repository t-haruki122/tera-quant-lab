"""Isolated databases and fail-closed network guards for every test."""

import os
import socket
from ipaddress import ip_address
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

ROOT = Path(__file__).resolve().parents[1]
REAL_DB = ROOT / "stock_data.db"


def file_stamp(path):
    return (path.stat().st_size, path.stat().st_mtime_ns) if path.exists() else None


DB_STAMP = file_stamp(REAL_DB)
# Collection may import database.py before fixtures run. Never even construct an
# engine pointing to the real database. Retain the directory for the whole run.
_bootstrap = TemporaryDirectory(prefix="tql-pytest-")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{Path(_bootstrap.name).as_posix()}/bootstrap.db"
os.environ["SECRET_KEY"] = "pytest-private-secret-key-not-the-development-default-123456789"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["CORS_ORIGINS"] = ""
os.environ["DOCS_ENABLED"] = "true"
os.environ["YFINANCE_CACHE_DIR"] = ""
os.environ["CACHE_WARM_ENABLED"] = "false"
os.environ["SYMBOL_DIRECTORY_ENABLED"] = "false"
os.environ["JQUANTS_API_KEY"] = ""
os.environ["JQUANTS_ENABLED"] = "true"


@pytest.fixture(scope="session", autouse=True)
def protect_real_database():
    yield
    assert file_stamp(REAL_DB) == DB_STAMP, "Tests changed stock_data.db!"
    _bootstrap.cleanup()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Unexpected network access: mock the client/transport in this test")

    import httpx
    import requests
    import yfinance
    from curl_cffi import requests as curl_requests
    from gnews import GNews

    # asyncio uses a loopback socketpair on Windows. Allow only literal loopback
    # socket addresses for that internal wakeup mechanism; HTTP remains blocked.
    def guard(original):
        def connect(sock, address):
            try:
                loopback = isinstance(address, tuple) and ip_address(address[0]).is_loopback
            except ValueError:
                loopback = False
            if loopback:
                return original(sock, address)
            return blocked(sock, address)

        return connect

    monkeypatch.setattr(socket.socket, "connect", guard(socket.socket.connect))
    monkeypatch.setattr(socket.socket, "connect_ex", guard(socket.socket.connect_ex))
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(requests.Session, "request", blocked)
    monkeypatch.setattr(curl_requests.Session, "request", blocked)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)
    monkeypatch.setattr(yfinance, "Ticker", blocked)
    monkeypatch.setattr(yfinance, "download", blocked)
    monkeypatch.setattr(yfinance, "Search", blocked)
    monkeypatch.setattr(GNews, "_fetch_feed", blocked)


@pytest.fixture(autouse=True)
def settings(monkeypatch, tmp_path):
    from app.config import get_settings
    from app.rate_limit import limiter

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    get_settings.cache_clear()
    limiter.reset()
    yield get_settings()
    limiter.reset()
    get_settings.cache_clear()


@pytest.fixture
def client(monkeypatch, settings):
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app import database, main

    engine = create_async_engine(settings.database_url)
    assert Path(engine.url.database).resolve() != REAL_DB.resolve()
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "AsyncSessionLocal", async_sessionmaker(engine, expire_on_commit=False))
    monkeypatch.setattr(database, "settings", settings)
    monkeypatch.setattr(main, "settings", settings)
    with TestClient(main.app) as test_client:
        try:
            yield test_client
        finally:
            test_client.portal.call(engine.dispose)


@pytest.fixture
def users(client):
    result = []
    for name in ("alice", "bob"):
        response = client.post("/user/register", json={"username": name, "password": "password123"})
        assert response.status_code == 200
        user = response.json()
        user["headers"] = {"Authorization": f"Bearer {user['access_token']}"}
        user["list_id"] = client.get(f"/user/{user['id']}/default-list", headers=user["headers"]).json()["id"]
        result.append(user)
    return result


@pytest.fixture
def async_db(monkeypatch, settings):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app import database

    engine = create_async_engine(settings.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "AsyncSessionLocal", sessions)
    return engine, sessions
