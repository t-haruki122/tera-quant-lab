"""Small process-local sliding-window limiter (no forwarded-header trust)."""

import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field

from fastapi import HTTPException, Request

from app.config import Settings


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
        with self._lock:
            now = time.monotonic()
            self._prune(now)
            key = ("register", ip)
            bucket = self._entries.get(key)
            if bucket and len(bucket.timestamps) >= settings.rate_limit_register_attempts:
                self._reject(bucket.timestamps[0] + bucket.seconds - now)
            bucket = self._entries.setdefault(key, _Window(settings.rate_limit_register_window_seconds))
            bucket.timestamps.append(now)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


limiter = SlidingWindowLimiter()
