import asyncio
import logging
import os
import re
import subprocess
import sys
from ipaddress import ip_network
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
import yfinance
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import database, main
from app.__main__ import build_uvicorn_config
from app.config import DEV_SECRET_KEY, Settings
from app.middleware import request_id
from app.rate_limit import client_ip, forwarded_scheme
from app.services import stock_service


def assert_security_headers(response):
    headers = response.headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Permissions-Policy"] == "camera=(), microphone=(), geolocation=()"
    assert headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert "strict-transport-security" not in headers
    csp = headers["Content-Security-Policy"]
    for directive in (
        "script-src 'self' https://cdn.jsdelivr.net", "style-src 'self' https://fonts.googleapis.com",
        "font-src https://fonts.gstatic.com", "connect-src 'self'", "img-src 'self' data:",
        "object-src 'none'", "base-uri 'self'", "form-action 'self'", "frame-ancestors 'none'",
    ):
        assert directive in csp.split("; ")
    assert "unsafe-eval" not in csp and "unsafe-inline" not in csp
    assert headers["X-Request-ID"]


@pytest.mark.parametrize(("path", "status"), [
    ("/health", 200), ("/", 200), ("/openapi.json", 200), ("/static/app.js", 200), ("/docs/missing", 404),
])
def test_security_headers_on_responses(client, path, status):
    response = client.get(path)
    assert response.status_code == status
    assert_security_headers(response)


@pytest.mark.parametrize(("path", "marker"), [
    ("/docs", "SwaggerUIBundle"), ("/redoc", "redoc.standalone.js"),
    ("/docs/oauth2-redirect", "swaggerUIRedirectOauth2"),
])
def test_scoped_csp_allows_docs_resources(client, path, marker):
    response = client.get(path)
    assert response.status_code == 200 and marker in response.text
    csp = response.headers["Content-Security-Policy"].split("; ")
    assert "script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'" in csp
    assert "style-src 'self' https://cdn.jsdelivr.net https://fonts.googleapis.com 'unsafe-inline'" in csp
    assert "img-src 'self' data: https://fastapi.tiangolo.com" in csp
    assert "worker-src 'self' blob:" in csp
    assert "connect-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "unsafe-eval" not in response.headers["Content-Security-Policy"]
    assert response.headers["X-Frame-Options"] == "DENY"


def test_docs_csp_with_proxy_root_path():
    client = TestClient(main.app, root_path="/prefix")
    try:
        response = client.get("/prefix/docs")
        assert response.status_code == 200 and "/prefix/openapi.json" in response.text
        assert "'unsafe-inline'" in response.headers["Content-Security-Policy"]
    finally:
        client.close()


def test_docs_setting_disables_interactive_docs_and_schema(tmp_path, settings):
    # The app registers its routes at import. Use a fresh interpreter and in-process ASGI requests.
    script = """
from fastapi.testclient import TestClient
from app.main import app
assert app.docs_url is app.redoc_url is app.openapi_url is app.swagger_ui_oauth2_redirect_url is None
client = TestClient(app)
for path in ('/docs', '/redoc', '/docs/oauth2-redirect', '/openapi.json'):
    response = client.get(path)
    assert response.status_code == 404, path
    assert 'unsafe-inline' not in response.headers['Content-Security-Policy'], path
"""
    env = {**os.environ, "DOCS_ENABLED": "false", "DATABASE_URL": settings.database_url,
           "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    proc = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("error", [RuntimeError("private trace detail"), HTTPException(500, "private trace detail")])
def test_generic_500_headers_and_correlated_logs(client, monkeypatch, caplog, error):
    async def failing_history(*args):
        assert request_id.get() == "from-client.123"
        raise error

    monkeypatch.setattr(stock_service, "get_history", failing_history)
    with caplog.at_level(logging.INFO):
        response = client.get("/stock/X/history", headers={"X-Request-ID": "from-client.123"})
    assert response.status_code == 500
    assert response.json() == {"detail": "サーバー内部でエラーが発生しました。しばらくしてから再試行してください。"}
    assert "private trace detail" not in response.text
    assert response.headers["X-Request-ID"] == "from-client.123"
    assert_security_headers(response)
    logs = [record for record in caplog.records if getattr(record, "request_id", None) == "from-client.123"]
    assert any(record.exc_info and "private trace detail" in str(record.exc_info[1]) for record in logs)
    assert any("GET /stock/X/history -> 500" in record.getMessage() for record in logs)
    assert request_id.get() == "-"


def test_request_id_generation_and_validation(client):
    assert client.get("/missing", headers={"X-Request-ID": "aZ09._-"}).headers["X-Request-ID"] == "aZ09._-"
    ids = [client.get("/health", headers=headers).headers["X-Request-ID"] for headers in (
        {}, {"X-Request-ID": "a" * 65}, {"X-Request-ID": "invalid ID"},
        [("X-Request-ID", "x"), ("X-Request-ID", "y")],
    )]
    assert len(set(ids)) == 4 and all(re.fullmatch("[0-9a-f]{32}", value) for value in ids)


def test_hsts_and_deployment_csp_override(client, settings):
    settings.hsts_enabled = True
    settings.content_security_policy = "default-src 'self'; frame-ancestors 'none'"
    headers = client.get("/missing").headers
    assert headers["Strict-Transport-Security"] == "max-age=31536000"
    assert headers["Content-Security-Policy"] == settings.content_security_policy
    with pytest.raises(ValidationError, match="ASCII"):
        Settings(_env_file=None, content_security_policy="default-src 'self'\r\nInvalid: value")


@pytest.mark.parametrize("path", ["/", "/static/app.js"])
def test_frontend_revalidates_cached_files(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-cache"
    assert response.headers["Last-Modified"]
    conditional = client.get(path, headers={"If-None-Match": response.headers["ETag"]})
    assert conditional.status_code == 304
    assert conditional.headers["Cache-Control"] == "no-cache"
    assert conditional.headers["ETag"] == response.headers["ETag"]
    assert conditional.content == b""


def test_missing_static_file_is_revalidated_too(client):
    response = client.get("/static/missing.js")
    assert response.status_code == 404
    assert response.headers["Cache-Control"] == "no-cache"
    assert "Cache-Control" not in client.get("/health").headers


def test_chart_sri():
    html = (main.FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    tag = re.search(r'<script\s+src="https://cdn\.jsdelivr\.net/npm/chart\.js@4\.4\.7/dist/chart\.umd\.min\.js"[^>]*>', html)
    assert tag
    assert 'integrity="sha384-vsrfeLOOY6KuIYKDlmVH5UiBmgIdB1oEf7p01YgWHuqmOHfZr374+odEv96n9tNC"' in tag[0]
    assert 'crossorigin="anonymous"' in tag[0]


def test_health_healthy_and_degraded(client, settings, monkeypatch):
    healthy = client.get("/health")
    assert healthy.status_code == 200
    assert healthy.json() == {"status": "ok", "version": settings.app_version,
                              "uptime_seconds": healthy.json()["uptime_seconds"], "database": "ok", "migration": "0009"}
    assert isinstance(healthy.json()["uptime_seconds"], int) and healthy.json()["uptime_seconds"] >= 0
    monkeypatch.setattr(database, "check_health", AsyncMock(side_effect=TimeoutError("database details")))
    degraded = client.get("/health")
    assert degraded.status_code == 503
    assert degraded.json() == {**healthy.json(), "status": "degraded", "database": "error", "migration": None,
                               "uptime_seconds": degraded.json()["uptime_seconds"]}
    assert_security_headers(degraded)


@pytest.mark.parametrize("configured", [True, False])
def test_yfinance_cache_directory_at_startup(configured, async_db, settings, tmp_path, monkeypatch):
    path = tmp_path / "data" / "yfinance-cache"
    monkeypatch.setattr(main, "settings", settings)
    settings.yfinance_cache_dir = str(path) if configured else ""

    def check_cache_dir(value):
        assert Path(value).is_dir()
        assert not Path(async_db[0].url.database).exists()

    set_cache = Mock(side_effect=check_cache_dir)
    monkeypatch.setattr(yfinance, "set_tz_cache_location", set_cache)
    assert not path.exists()
    with TestClient(main.app):
        if configured:
            set_cache.assert_called_once_with(str(path.resolve()))
        else:
            set_cache.assert_not_called()
            assert not path.exists()


@pytest.mark.parametrize("secret_key", [DEV_SECRET_KEY, "short-key"])
@pytest.mark.parametrize("app_env", ["production", "development"])
def test_secret_key_startup_guard(app_env, secret_key, settings, async_db, monkeypatch, caplog):
    monkeypatch.setattr(main, "settings", settings)
    settings.app_env = app_env
    settings.secret_key = secret_key

    async def start():
        async with main.lifespan(main.app):
            assert await database.check_health() == "0009"

    if app_env == "production":
        with pytest.raises(RuntimeError, match=r"APP_ENV=production: SECRET_KEY .*32文字"):
            asyncio.run(start())
        assert not Path(async_db[0].url.database).exists()
    else:
        asyncio.run(start())
        assert "SECRET_KEY に既定値以外の32文字以上" in caplog.text


def test_production_wildcard_cors_warning(settings, async_db, monkeypatch, caplog):
    monkeypatch.setattr(main, "settings", settings)
    settings.app_env = "production"
    settings.cors_origins = ["*"]

    async def start():
        async with main.lifespan(main.app):
            assert await database.check_health() == "0009"

    asyncio.run(start())
    assert 'CORS_ORIGINS に "*"' in caplog.text


@pytest.mark.parametrize("absolute", [False, True])
def test_db_directory_created_at_startup(tmp_path, monkeypatch, absolute):
    monkeypatch.chdir(tmp_path)
    relative = Path("new") / "nested" / "test.db"
    db_path = tmp_path / relative if absolute else relative
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "AsyncSessionLocal", async_sessionmaker(engine))
    assert not db_path.parent.exists()

    async def start():
        try:
            await database.init_db()
            assert await database.check_health() == "0009"
        finally:
            await engine.dispose()

    asyncio.run(start())
    assert db_path.is_file()
    assert make_url("sqlite+aiosqlite:////var/lib/tera-quant-lab/stock_data.db").database == "/var/lib/tera-quant-lab/stock_data.db"


def test_trusted_proxies_from_env_and_uvicorn_config(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXIES", " 192.0.2.10, 10.0.0.0/8, 2001:db8::/32, ")
    monkeypatch.setenv("WEB_CONCURRENCY", "12")
    default = Settings(_env_file=None)
    assert default.host == "127.0.0.1" and default.port == 8000
    settings = Settings(_env_file=None, host="0.0.0.0", port=8767, log_level="WARNING")
    assert settings.trusted_proxies == [ip_network("192.0.2.10"), ip_network("10.0.0.0/8"), ip_network("2001:db8::/32")]
    config = build_uvicorn_config(settings)
    assert (config.app, config.host, config.port, config.log_level) == ("app.main:app", "0.0.0.0", 8767, "warning")
    assert config.workers == 1 and config.lifespan == "on" and not config.proxy_headers
    assert config.forwarded_allow_ips == ["192.0.2.10/32", "10.0.0.0/8", "2001:db8::/32"]
    monkeypatch.setenv("TRUSTED_PROXIES", "*")
    with pytest.raises(ValidationError, match="trusted_proxies"):
        Settings(_env_file=None)


@pytest.mark.parametrize(("peer", "chain", "expected"), [
    ("203.0.113.3", ["192.0.2.4"], "203.0.113.3"),
    ("10.0.0.1", ["198.51.100.8, 203.0.113.3, 10.0.0.2"], "203.0.113.3"),
    ("10.0.0.1", ["198.51.100.8", "203.0.113.3, 10.0.0.2"], "203.0.113.3"),
    ("10.0.0.1", ["203.0.113.3, bogus, 10.0.0.2"], "10.0.0.1"),
    ("10.0.0.1", ["10.0.0.2"], "10.0.0.1"),
    ("2001:db8:abcd::a", ["2001:db8::50, 2001:db8:abcd::b"], "2001:db8::50"),
])
def test_client_ip_trust_chain_and_ipv6(peer, chain, expected):
    settings = Settings(_env_file=None, trusted_proxies="10.0.0.0/8,2001:db8:abcd::/48")
    headers = [(b"x-forwarded-for", line.encode()) for line in chain] + [(b"x-forwarded-proto", b"https")]
    request = Request({"type": "http", "client": (peer, 50000), "headers": headers})
    assert client_ip(request, settings) == expected
    assert forwarded_scheme(request, settings) == (None if peer == "203.0.113.3" else "https")
