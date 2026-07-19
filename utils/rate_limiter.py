import re
import time
from collections import defaultdict, deque
from functools import wraps

from flask import flash, redirect, request, session, url_for
from config.settings import Config

try:
    from flask_limiter import Limiter  # type: ignore
    from flask_limiter.util import get_remote_address  # type: ignore
except Exception:  # pragma: no cover
    Limiter = None
    get_remote_address = None


def _parse_limit(limit_value: str):
    match = re.search(r"(\d+)\s*(?:/|per\s+)(second|minute|hour|day)s?", str(limit_value), re.I)
    if not match:
        return 5, 60

    count = int(match.group(1))
    period = match.group(2).lower()
    seconds = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}.get(period, 60)
    return count, seconds


class _FallbackLimiter:
    def __init__(self):
        self._hits = defaultdict(deque)

    def _identity(self):
        return (
            session.get("mobile")
            or session.get("user_id")
            or request.form.get("mobile")
            or request.args.get("mobile")
            or request.remote_addr
            or "anonymous"
        )

    def limit(self, limit_value: str, redirect_endpoint: str = "auth.login", message: str = "Too many requests. Please try again later."):
        max_hits, window_seconds = _parse_limit(limit_value)

        def decorator(view_func):
            @wraps(view_func)
            def wrapped(*args, **kwargs):
                key = f"{request.endpoint}:{self._identity()}"
                now = time.time()
                hits = self._hits[key]

                while hits and now - hits[0] > window_seconds:
                    hits.popleft()

                if len(hits) >= max_hits:
                    flash(message, "danger")
                    return redirect(url_for(redirect_endpoint))

                hits.append(now)
                return view_func(*args, **kwargs)

            return wrapped

        return decorator


if Limiter is not None and Config.RATELIMIT_STORAGE_URI and not str(Config.RATELIMIT_STORAGE_URI).startswith("memory://"):
    limiter = Limiter(
        key_func=get_remote_address,
        default_limits=[],
        storage_uri=Config.RATELIMIT_STORAGE_URI,
    )

    def limit_route(limit_value: str, redirect_endpoint: str = "auth.login", message: str = "Too many requests. Please try again later."):
        def decorator(view_func):
            @limiter.limit(limit_value)
            @wraps(view_func)
            def wrapped(*args, **kwargs):
                return view_func(*args, **kwargs)

            return wrapped

        return decorator

else:
    limiter = _FallbackLimiter()

    def limit_route(limit_value: str, redirect_endpoint: str = "auth.login", message: str = "Too many requests. Please try again later."):
        return limiter.limit(limit_value, redirect_endpoint=redirect_endpoint, message=message)
