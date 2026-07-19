from __future__ import annotations

import json
import time
from dataclasses import dataclass
from functools import wraps
from threading import RLock
from typing import Any, Callable, TypeVar

from config.settings import Config

T = TypeVar("T")


@dataclass
class _CacheEntry:
    value: Any
    expires_at: float


class CacheManager:
    """Small cache facade with optional Redis backing and in-memory fallback."""

    def __init__(self):
        self._lock = RLock()
        self._memory: dict[str, _CacheEntry] = {}
        self._redis = None

    def _get_redis(self):
        if self._redis is not None:
            return self._redis
        try:
            import redis

            if not getattr(Config, "REDIS_URL", None):
                return None
            self._redis = redis.from_url(Config.REDIS_URL)
            return self._redis
        except Exception:
            self._redis = None
            return None

    def get(self, key: str):
        redis_client = self._get_redis()
        if redis_client is not None:
            try:
                value = redis_client.get(key)
                if value is None:
                    return None
                return json.loads(value)
            except Exception:
                pass

        now = time.time()
        with self._lock:
            entry = self._memory.get(key)
            if not entry:
                return None
            if entry.expires_at and entry.expires_at < now:
                self._memory.pop(key, None)
                return None
            return entry.value

    def set(self, key: str, value: Any, ttl_seconds: int = 300):
        redis_client = self._get_redis()
        if redis_client is not None:
            try:
                redis_client.setex(key, ttl_seconds, json.dumps(value, default=str))
                return value
            except Exception:
                pass

        with self._lock:
            self._memory[key] = _CacheEntry(value=value, expires_at=time.time() + ttl_seconds)
        return value

    def delete(self, key: str):
        redis_client = self._get_redis()
        if redis_client is not None:
            try:
                redis_client.delete(key)
            except Exception:
                pass
        with self._lock:
            self._memory.pop(key, None)


cache_manager = CacheManager()


def ttl_cache(prefix: str, ttl_seconds: int, key_builder: Callable[..., str]):
    """Decorator for lightweight TTL caching."""

    def decorator(func: Callable[..., T]):
        @wraps(func)
        def wrapped(*args, **kwargs):
            cache_key = f"{prefix}:{key_builder(*args, **kwargs)}"
            cached = cache_manager.get(cache_key)
            if cached is not None:
                return cached
            result = func(*args, **kwargs)
            cache_manager.set(cache_key, result, ttl_seconds=ttl_seconds)
            return result

        return wrapped

    return decorator

