"""Redis-backed sliding-window rate limiter."""
import time
import uuid

from fastapi import HTTPException

from app.config import settings
from app.redis_store import get_redis


WINDOW_SECONDS = 60


def check_rate_limit(user_id: str) -> dict[str, int]:
    now = time.time()
    key = f"rate:{user_id}"
    client = get_redis()

    client.zremrangebyscore(key, 0, now - WINDOW_SECONDS)
    count = int(client.zcard(key))
    remaining = settings.rate_limit_per_minute - count

    if count >= settings.rate_limit_per_minute:
        retry_after = WINDOW_SECONDS
        raise HTTPException(
            status_code=429,
            detail={
                "error": "Rate limit exceeded",
                "limit": settings.rate_limit_per_minute,
                "window_seconds": WINDOW_SECONDS,
            },
            headers={
                "X-RateLimit-Limit": str(settings.rate_limit_per_minute),
                "X-RateLimit-Remaining": "0",
                "Retry-After": str(retry_after),
            },
        )

    client.zadd(key, {f"{now}:{uuid.uuid4().hex}": now})
    client.expire(key, WINDOW_SECONDS + 5)

    return {
        "limit": settings.rate_limit_per_minute,
        "remaining": max(0, remaining - 1),
        "reset_seconds": WINDOW_SECONDS,
    }
