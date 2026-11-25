"""Process-local sliding windows and trusted client IP resolution."""

import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv6Address, ip_address

from fastapi import HTTPException, Request

from app.config import Settings, get_settings


@dataclass
class _Window:
    seconds: int
    timestamps: deque[float] = field(default_factory=deque)


class SlidingWindowLimiter:
    """Lock every state access; prune all buckets, including inactive keys.

    Login failures are recorded on completion, so in-flight checks are not
    miscounted as failures. IP admission is atomic and bounds concurrent work.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, ...], _Window] = {}

    def _prune(self, now: float) -> None:
        for key, bucket in list(self._entries.items()):
            while bucket.timestamps and bucket.timestamps[0] <= now - bucket.seconds:
                bucket.timestamps.popleft()
            if not bucket.timestamps:
                del self._entries[key]

    def reset(self) -> None:
        with self._lock:
            self._entries.clear()

    @staticmethod
    def _reject(seconds: float) -> None:
        raise HTTPException(
            status_code=429,
            detail="試行回数が多すぎます。しばらくしてから再試行してください。",
            headers={"Retry-After": str(max(1, math.ceil(seconds)))},
        )

    def begin_login(self, ip: str, username: str, settings: Settings) -> None:
        with self._lock:
            now = time.monotonic()
            self._prune(now)
            ip_key = ("login-ip", ip)
            bucket = self._entries.get(ip_key)
            if bucket and len(bucket.timestamps) >= settings.rate_limit_login_ip_attempts:
                self._reject(bucket.timestamps[0] + bucket.seconds - now)
            bucket = self._entries.setdefault(
                ip_key, _Window(settings.rate_limit_login_ip_window_seconds)
            )
            bucket.timestamps.append(now)
            # Even attempts blocked by the username limit count toward the IP cap.
            failed = self._entries.get(("login-failure", ip, username.lower()))
            if failed and len(failed.timestamps) >= settings.rate_limit_login_failures:
                self._reject(failed.timestamps[0] + failed.seconds - now)

    def finish_login(self, ip: str, username: str, settings: Settings, *, success: bool) -> None:
        with self._lock:
            now = time.monotonic()
            self._prune(now)
            key = ("login-failure", ip, username.lower())
            if success:
                self._entries.pop(key, None)
            else:
                bucket = self._entries.setdefault(key, _Window(settings.rate_limit_login_window_seconds))
                bucket.timestamps.append(now)

    def register(self, ip: str, settings: Settings) -> None:
        self._admit(("register", ip), settings.rate_limit_register_attempts, settings.rate_limit_register_window_seconds)

    def data(self, request: Request, settings: Settings, *, user_id: int | None = None) -> None:
        if settings.rate_limit_enabled:
            key = ("data-user", str(user_id)) if user_id is not None else ("data-ip", client_ip(request, settings))
            self._admit(key, settings.rate_limit_data_requests, settings.rate_limit_data_window_seconds)

    def _admit(self, key: tuple[str, ...], limit: int, seconds: int) -> None:
        with self._lock:
            now = time.monotonic()
            self._prune(now)
            bucket = self._entries.get(key)
            if bucket and len(bucket.timestamps) >= limit:
                self._reject(bucket.timestamps[0] + bucket.seconds - now)
            bucket = self._entries.setdefault(key, _Window(seconds))
            bucket.timestamps.append(now)


def _address(value: str) -> IPv4Address | IPv6Address | None:
    if "%" in value:  # An XFF entry is an unscoped IP literal.
        return None
    try:
        return ip_address(value)
    except ValueError:
        return None


def _is_trusted_proxy(address: IPv4Address | IPv6Address, settings: Settings) -> bool:
    return any(address in network for network in settings.trusted_proxies)


def client_ip(request: Request, settings: Settings | None = None) -> str:
    peer = request.client.host if request.client else "unknown"
    address = _address(peer)
    if address is None:
        return peer
    settings = settings or get_settings()
    if _is_trusted_proxy(address, settings):
        for header in reversed(request.headers.getlist("x-forwarded-for")):
            for value in reversed(header.split(",")):
                forwarded = _address(value.strip())
                if forwarded is None:
                    # The verified chain ends here. Anything further left can be spoofed.
                    return peer
                if not _is_trusted_proxy(forwarded, settings):
                    return str(forwarded)
    return peer


def forwarded_scheme(request: Request, settings: Settings) -> str | None:
    values = request.headers.getlist("x-forwarded-proto")
    if len(values) == 1 and values[0] in {"http", "https"} and request.client:
        address = _address(request.client.host)
        if address is not None and _is_trusted_proxy(address, settings):
            return values[0]
    return None


limiter = SlidingWindowLimiter()
