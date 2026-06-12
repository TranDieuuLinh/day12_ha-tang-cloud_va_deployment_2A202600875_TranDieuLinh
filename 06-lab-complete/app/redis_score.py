"""Redis access helpers with a development fallback.

Production deployments should set REDIS_REQUIRED=true so readiness fails when
Redis is unavailable. The fallback keeps local examples easy to run offline.
"""
from __future__ import annotations

import fnmatch
import time
from collections import defaultdict
from threading import RLock
from typing import Any

import redis

from app.config import settings


class MemoryRedis:
    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._expires: dict[str, float] = {}
        self._lock = RLock()

    def _purge(self, key: str) -> None:
        expires_at = self._expires.get(key)
        if expires_at is not None and expires_at <= time.time():
            self._data.pop(key, None)
            self._expires.pop(key, None)

    def ping(self) -> bool:
        return True

    def get(self, key: str) -> Any:
        with self._lock:
            self._purge(key)
            return self._data.get(key)

    def setex(self, key: str, ttl: int, value: Any) -> bool:
        with self._lock:
            self._data[key] = value
            self._expires[key] = time.time() + ttl
            return True

    def incrbyfloat(self, key: str, amount: float) -> float:
        with self._lock:
            self._purge(key)
            current = float(self._data.get(key) or 0)
            current += amount
            self._data[key] = current
            return current

    def expire(self, key: str, ttl: int) -> bool:
        with self._lock:
            if key in self._data:
                self._expires[key] = time.time() + ttl
                return True
            return False

    def zadd(self, key: str, mapping: dict[str, float]) -> int:
        with self._lock:
            self._purge(key)
            bucket = self._data.setdefault(key, {})
            added = 0
            for member, score in mapping.items():
                if member not in bucket:
                    added += 1
                bucket[member] = score
            return added

    def zremrangebyscore(self, key: str, minimum: float, maximum: float) -> int:
        with self._lock:
            self._purge(key)
            bucket = self._data.setdefault(key, {})
            removed = [member for member,
                       score in bucket.items() if minimum <= score <= maximum]
            for member in removed:
                bucket.pop(member, None)
            return len(removed)

    def zcard(self, key: str) -> int:
        with self._lock:
            self._purge(key)
            return len(self._data.get(key, {}))

    def delete(self, key: str) -> int:
        with self._lock:
            existed = key in self._data
            self._data.pop(key, None)
            self._expires.pop(key, None)
            return int(existed)

    def scan_iter(self, match: str = "*"):
        with self._lock:
            for key in list(self._data.keys()):
                self._purge(key)
                if fnmatch.fnmatch(key, match):
                    yield key


_redis_client: redis.Redis | MemoryRedis | None = None
_using_fallback = False
_last_error = ""


def get_redis() -> redis.Redis | MemoryRedis:
    global _redis_client, _using_fallback, _last_error
    if _redis_client is not None:
        return _redis_client

    try:
        client = redis.from_url(settings.redis_url, decode_responses=True)
        client.ping()
        _redis_client = client
        _using_fallback = False
        _last_error = ""
    except Exception as exc:
        _last_error = str(exc)
        if settings.redis_required:
            raise
        _redis_client = MemoryRedis()
        _using_fallback = True
    return _redis_client


def redis_status() -> dict[str, Any]:
    try:
        client = get_redis()
        client.ping()
        return {
            "ok": True,
            "backend": "memory" if _using_fallback else "redis",
            "required": settings.redis_required,
            "error": _last_error,
        }
    except Exception as exc:
        return {
            "ok": False,
            "backend": "redis",
            "required": settings.redis_required,
            "error": str(exc),
        }
