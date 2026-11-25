import json
import os
import socket
import subprocess
import sys

import pytest
import requests


def test_standalone_database_initialization(settings):
    # A fresh process ensures model registration does not depend on collection
    # order or the router imports performed by app.main.
    script = """
import asyncio, json
from sqlalchemy import inspect
from app.database import engine, init_db
async def main():
    await init_db()
    async with engine.connect() as connection:
        tables = await connection.run_sync(lambda conn: inspect(conn).get_table_names())
    await engine.dispose()
    print(json.dumps(tables))
asyncio.run(main())
"""
    env = os.environ.copy()
    env["DATABASE_URL"] = settings.database_url
    result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True,
                            text=True, encoding="utf-8", check=True, timeout=30)
    assert {"users", "user_lists", "user_list_items", "stock_memos"} <= set(json.loads(result.stdout))


def test_network_guard_rejects_real_http_and_external_sockets():
    with pytest.raises(AssertionError, match="Unexpected network access"):
        requests.get("https://example.invalid")
    for address in (("203.0.113.1", 443), ("example.invalid", 443)):
        with socket.socket() as sock, pytest.raises(AssertionError, match="Unexpected network access"):
            sock.connect(address)
