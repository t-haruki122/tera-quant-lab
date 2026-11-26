"""The HTTP boundary for the read-only MCP tools."""

import asyncio
import math
import os
import re
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

type JSONObject = dict[str, Any]

_GET_PATHS = re.compile(
    r"/stock/(?:[A-Z0-9]|%5E)(?:[A-Z0-9.\-]|%5E|%3D){0,19}"
    r"(?:/history|/profile|/indicators|/financials(?:/history)?|/news)?"
    r"|/forex/[A-Z]{6}(?:%3DX)?"
    r"|/user/[1-9][0-9]*/(?:lists(?:/[1-9][0-9]*/summary)?|alerts)"
    r"|/backtest/(?:strategies|runs(?:/[1-9][0-9]*)?)"
)
_POST_PATHS = {"/user/login", "/backtest/run"}


@dataclass(frozen=True)
class Settings:
    base_url: str = "http://127.0.0.1:8000"
    username: str | None = field(default=None, repr=False)
    password: str | None = field(default=None, repr=False)
    timeout_seconds: float = 30

    def __post_init__(self) -> None:
        try:
            url = httpx.URL(self.base_url.strip().rstrip("/"))
        except (httpx.InvalidURL, ValueError):
            raise ValueError("TQL_BASE_URL に有効な http または https の URL を指定してください。") from None
        if url.scheme not in {"http", "https"} or not url.host or url.userinfo or url.query or url.fragment:
            raise ValueError("TQL_BASE_URL に認証情報やクエリ、フラグメントを含まない http または https の URL を指定してください。")
        object.__setattr__(self, "base_url", str(url).rstrip("/"))
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("TQL_TIMEOUT_SECONDS は正の秒数を指定してください。")

    @classmethod
    def from_env(cls) -> "Settings":
        try:
            timeout = float(os.environ.get("TQL_TIMEOUT_SECONDS", "30"))
        except ValueError:
            raise ValueError("TQL_TIMEOUT_SECONDS は正の秒数を指定してください。") from None
        return cls(
            base_url=os.environ.get("TQL_BASE_URL", "http://127.0.0.1:8000"),
            username=os.environ.get("TQL_USERNAME"),
            password=os.environ.get("TQL_PASSWORD"),
            timeout_seconds=timeout,
        )


@dataclass(frozen=True)
class _Login:
    user_id: int
    token: str = field(repr=False)


class TQLClient:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._http = httpx.AsyncClient(
            base_url=settings.base_url, timeout=settings.timeout_seconds, transport=transport,
            follow_redirects=False, trust_env=False,
        )
        self._login: _Login | None = None
        self._login_lock = asyncio.Lock()

    async def __aenter__(self) -> "TQLClient":
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None,
    ) -> None:
        self._login = None
        await self._http.aclose()

    async def _send(
        self, method: str, path: str, *, params: dict[str, str] | None = None,
        body: JSONObject | None = None, login: _Login | None = None,
    ) -> httpx.Response:
        if not ((method == "GET" and _GET_PATHS.fullmatch(path)) or (method == "POST" and path in _POST_PATHS)):
            raise ToolError("読み取り専用 MCP で許可されていない API です。")
        headers = {"Authorization": f"Bearer {login.token}"} if login else None
        timeout = self._settings.timeout_seconds
        if path == "/backtest/run":
            timeout = max(timeout, 120)
        try:
            return await self._http.request(method, path, params=params, json=body, headers=headers, timeout=timeout)
        except httpx.RequestError:
            message = f"アプリに接続できません（TQL_BASE_URL={self._settings.base_url}）。サーバーが起動しているか確認してください。"
            raise ToolError(self._redact(message, login)) from None

    def _redact(self, text: str, *logins: _Login | None) -> str:
        secrets = {self._settings.password, *(login.token for login in (*logins, self._login) if login is not None)}
        for secret in sorted(filter(None, secrets), key=len, reverse=True):
            text = text.replace(secret, "[非表示]")
        return text

    def _read_json(self, response: httpx.Response, *logins: _Login | None) -> JSONObject:
        try:
            data = response.json()
        except ValueError:
            data = None
        if not response.is_success:
            detail = data.get("detail") if isinstance(data, dict) else None
            if isinstance(detail, list):
                # FastAPI validation errors can echo submitted values in `input`.
                detail = " / ".join(
                    item["msg"] for item in detail if isinstance(item, dict) and isinstance(item.get("msg"), str)
                )
            message = f"HTTP {response.status_code} {detail if isinstance(detail, str) and detail else 'API リクエストに失敗しました。'}"
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                retry = f"Retry-After: {retry_after}" if retry_after else "Retry-After ヘッダーはありません"
                message += f"（{retry}。時間をおいて再試行してください。）"
            raise ToolError(self._redact(message, *logins))
        if not isinstance(data, dict):
            raise ToolError("アプリからの応答が JSON オブジェクトではありません。")
        return data

    async def _authenticate(self, rejected: _Login | None = None) -> _Login:
        async with self._login_lock:
            if self._login is not None and self._login is not rejected:
                return self._login
            username, password = self._settings.username, self._settings.password
            if not username or not password:
                raise ToolError("このツールにはログインが必要です。TQL_USERNAME と TQL_PASSWORD を設定してください。")
            response = await self._send("POST", "/user/login", body={"username": username, "password": password})
            data = self._read_json(response, rejected)
            user_id, token = data.get("id"), data.get("access_token")
            if type(user_id) is not int or user_id <= 0 or not isinstance(token, str) or not token:
                raise ToolError("アプリのログイン応答に有効なユーザー ID とトークンがありません。")
            self._login = _Login(user_id, token)
            return self._login

    async def get(self, path: str, *, params: dict[str, str] | None = None) -> JSONObject:
        return self._read_json(await self._send("GET", path, params=params))

    async def _authenticated_request(
        self, method: str, path: str, *, body: JSONObject | None = None,
    ) -> JSONObject:
        login = await self._authenticate()
        response = await self._send(method, path.format(user_id=login.user_id), body=body, login=login)
        if response.status_code == 401:
            fresh = await self._authenticate(rejected=login)
            response = await self._send(method, path.format(user_id=fresh.user_id), body=body, login=fresh)
            return self._read_json(response, fresh, login)
        return self._read_json(response, login)

    async def get_user(self, suffix: str) -> JSONObject:
        return await self._authenticated_request("GET", "/user/{user_id}" + suffix)

    async def get_saved_run(self, suffix: str = "") -> JSONObject:
        return await self._authenticated_request("GET", "/backtest/runs" + suffix)

    async def run_backtest(self, body: JSONObject) -> JSONObject:
        return await self._authenticated_request("POST", "/backtest/run", body=body)
