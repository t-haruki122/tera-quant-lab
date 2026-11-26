import hashlib
import os
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import jwt
import pytest
from argon2 import PasswordHasher

from app.services import user_service

USER_ROUTES = [
    ("GET", "/default-list", None),
    ("GET", "/lists", None),
    ("POST", "/lists", {"name": "private"}),
    ("GET", "/lists/{list_id}", None),
    ("PUT", "/lists/{list_id}", {"name": "renamed"}),
    ("DELETE", "/lists/{list_id}", None),
    ("POST", "/lists/{list_id}/items", {"symbol": "AAPL", "tags": []}),
    ("DELETE", "/lists/{list_id}/items/AAPL", None),
    ("PUT", "/lists/{list_id}/items/AAPL/tags", {"tags": ["private"]}),
    ("GET", "/memo/AAPL", None),
    ("PUT", "/memo/AAPL", {"memo": "private"}),
    ("POST", "/password", {"current_password": "password123", "new_password": "new-password"}),
    ("POST", "/logout-all", None),
    ("DELETE", "", {"current_password": "password123"}),
    ("GET", "/lists/{list_id}/summary", None),
    ("GET", "/lists/{list_id}/export.csv", None),
    ("GET", "/export", None),
    ("POST", "/import", {}),
]


@pytest.mark.parametrize("method,suffix,body", USER_ROUTES)
def test_all_user_routes_require_owner(client, users, method, suffix, body):
    alice, bob = users
    path = f"/user/{alice['id']}" + suffix.format(list_id=alice["list_id"])
    missing = client.request(method, path, json=body)
    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert client.request(method, path, json=body, headers=bob["headers"]).status_code == 403


def test_register_login_and_token(client, users):
    alice, _ = users
    assert alice["token_type"] == "bearer"
    claims = jwt.decode(alice["access_token"], os.environ["SECRET_KEY"], algorithms=["HS256"])
    assert claims["sub"] == str(alice["id"])
    assert claims["ver"] == 0
    assert claims["exp"] > claims["iat"]
    login = client.post("/user/login", json={"username": "alice", "password": "password123"})
    assert login.status_code == 200
    assert login.json()["access_token"]
    assert login.json()["token_type"] == "bearer"
    assert client.get(f"/user/{alice['id']}/lists", headers=alice["headers"]).status_code == 200
    assert client.post("/user/register", json={"username": "alice", "password": "password123"}).status_code == 409


def test_short_registration_password(client):
    response = client.post("/user/register", json={"username": "short", "password": "1234567"})
    assert response.status_code == 400
    assert response.json()["detail"] == "パスワードは8文字以上にしてください"


def test_ownership_of_lists_items_tags_and_memos(client, users):
    alice, bob = users
    own = f"/user/{alice['id']}"
    alien_list = f"{own}/lists/{bob['list_id']}"
    for method, suffix, body in USER_ROUTES[3:9]:
        path = own + suffix.format(list_id=bob["list_id"])
        expected = 400 if method == "POST" else 404
        assert client.request(method, path, headers=alice["headers"], json=body).status_code == expected
    assert client.get(alien_list, headers=alice["headers"]).status_code == 404
    untouched = client.get(f"/user/{bob['id']}/lists/{bob['list_id']}", headers=bob["headers"]).json()
    assert untouched["name"] == "マイリスト"
    assert untouched["items"] == []
    path = f"{own}/lists/{alice['list_id']}"
    assert client.post(path + "/items", headers=alice["headers"], json={"symbol": "AAPL"}).status_code == 200
    response = client.put(path + "/items/AAPL/tags", headers=alice["headers"], json={"tags": ["mine"]})
    assert response.json()["tags"] == ["mine"]
    assert client.put(own + "/memo/AAPL", headers=alice["headers"], json={"memo": "secret"}).status_code == 200
    assert client.get(own + "/memo/AAPL", headers=alice["headers"]).json()["memo"] == "secret"
    assert client.get(f"/user/{bob['id']}/memo/AAPL", headers=bob["headers"]).json()["memo"] is None
    assert client.get(path, headers=alice["headers"]).json()["items"] == [{"symbol": "AAPL", "tags": ["mine"], "shares": None}]
    assert client.delete(path + "/items/AAPL", headers=alice["headers"]).status_code == 200
    created = client.post(own + "/lists", headers=alice["headers"], json={"name": "new"}).json()
    new_path = f"{own}/lists/{created['id']}"
    assert client.put(new_path, headers=alice["headers"], json={"name": "changed"}).json()["name"] == "changed"
    assert client.delete(new_path, headers=alice["headers"]).status_code == 200


def read_hash(settings, username):
    from sqlalchemy.engine import make_url

    with closing(sqlite3.connect(Path(make_url(settings.database_url).database))) as db:
        return db.execute("SELECT password_hash FROM users WHERE username=?", (username,)).fetchone()[0]


@pytest.mark.parametrize("kind", ["legacy", "rehash"])
def test_password_hash_upgrade(client, users, settings, kind):
    from sqlalchemy.engine import make_url

    password = "old" if kind == "legacy" else "rehash-me"
    old_hash = (hashlib.sha256(password.encode()).hexdigest() if kind == "legacy" else
                PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1).hash(password))
    with closing(sqlite3.connect(make_url(settings.database_url).database)) as db, db:
        db.execute("INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                   (kind, old_hash, datetime.now(UTC).isoformat()))
    assert client.post("/user/login", json={"username": kind, "password": password}).status_code == 200
    updated = read_hash(settings, kind)
    assert updated != old_hash
    assert updated.startswith("$argon2")
    assert user_service.ph.verify(updated, password)
    assert not user_service.ph.check_needs_rehash(updated)
    current_hash = read_hash(settings, "alice")
    assert user_service.ph.verify(current_hash, "password123")
    assert client.post("/user/login", json={"username": "alice", "password": "password123"}).status_code == 200
    assert read_hash(settings, "alice") == current_hash


def test_unknown_user_dummy_hash_runs_in_worker(client, users):
    wrong = client.post("/user/login", json={"username": "alice", "password": "incorrect"})
    original = PasswordHasher.verify
    threads = []

    def spy(hasher, stored, password):
        threads.append(threading.get_ident())
        return original(hasher, stored, password)

    with patch.object(PasswordHasher, "verify", autospec=True, side_effect=spy) as verify:
        missing = client.post("/user/login", json={"username": "missing", "password": "incorrect"})
    assert missing.status_code == wrong.status_code == 401
    assert missing.json() == wrong.json()
    verify.assert_called_once_with(user_service.ph, user_service._DUMMY_HASH, "incorrect")
    assert threads[0] != client.portal.call(threading.get_ident)
    match = client.post("/user/login", json={"username": "missing", "password": "tql-dummy-password-for-missing-users"})
    assert match.status_code == 401
    assert match.json() == wrong.json()


@pytest.mark.parametrize("kind", ["malformed", "expired", "signature", "unknown", "non-id", "missing-claims", "overflow"])
def test_invalid_tokens(client, users, kind):
    alice, _ = users
    now = datetime.now(UTC)
    claims = {"sub": str(alice["id"]), "iat": now, "exp": now + timedelta(minutes=5)}
    key = os.environ["SECRET_KEY"]
    if kind == "expired":
        claims["exp"] = now - timedelta(seconds=5)
    elif kind == "signature":
        key = "another-private-secret-key-with-at-least-32-bytes"
    elif kind in {"unknown", "non-id", "overflow"}:
        claims["sub"] = {"unknown": "999999", "non-id": "not-an-id", "overflow": str(2**64)}[kind]
    elif kind == "missing-claims":
        claims = {"sub": str(alice["id"])}
    token = "not-a-token" if kind == "malformed" else jwt.encode(claims, key, algorithm="HS256")
    response = client.get(f"/user/{alice['id']}/lists", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
