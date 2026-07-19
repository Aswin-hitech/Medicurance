import re
import time
from collections import defaultdict, deque
from functools import wraps

from flask import request, current_app

try:
    from flask_limiter import Limiter as FlaskLimiter  # type: ignore
    from flask_limiter.util import get_remote_address  # type: ignore
except Exception:  # pragma: no cover
    FlaskLimiter = None
    get_remote_address = None


_memory_buckets = defaultdict(deque)


class SimpleLimiter:
    def __init__(self, app=None):
        self.app = app

    def init_app(self, app):
        self.app = app

    def limit(self, rule):
        count, window = self._parse_rule(rule)

        def decorator(fn):
            @wraps(fn)
            def wrapped(*args, **kwargs):
                key = f"{request.endpoint}:{request.remote_addr or 'unknown'}"
                now = time.time()
                bucket = _memory_buckets[key]
                while bucket and now - bucket[0] > window:
                    bucket.popleft()
                if len(bucket) >= count:
                    return current_app.response_class(
                        "Too many requests", status=429
                    )
                bucket.append(now)
                return fn(*args, **kwargs)

            return wrapped

        return decorator

    @staticmethod
    def _parse_rule(rule):
        text = str(rule).lower().strip()
        match = re.search(r"(\d+)", text)
        count = int(match.group(1)) if match else 5
        if "minute" in text:
            window = 60
        elif "hour" in text:
            window = 3600
        else:
            window = 60
        return count, window


def create_limiter(app=None):
    if FlaskLimiter is not None:
        limiter = FlaskLimiter(
            key_func=get_remote_address,
            default_limits=[],
            storage_uri="memory://",
        )
        if app is not None:
            limiter.init_app(app)
        return limiter

    limiter = SimpleLimiter(app)
    if app is not None:
        limiter.init_app(app)
    return limiter

