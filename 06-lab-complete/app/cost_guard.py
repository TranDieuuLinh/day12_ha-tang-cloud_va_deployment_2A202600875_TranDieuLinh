"""Monthly budget guard stored in Redis."""
from datetime import datetime, timezone

from fastapi import HTTPException

from app.config import settings
from app.redis_store import get_redis


PRICE_PER_1K_INPUT_TOKENS = 0.00015
PRICE_PER_1K_OUTPUT_TOKENS = 0.00060
MONTH_TTL_SECONDS = 32 * 24 * 60 * 60


def estimate_cost(input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens / 1000 * PRICE_PER_1K_INPUT_TOKENS
        + output_tokens / 1000 * PRICE_PER_1K_OUTPUT_TOKENS
    )


def _month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def check_budget(user_id: str, estimated_cost: float = 0.0) -> dict[str, float | str]:
    client = get_redis()
    month = _month_key()
    user_key = f"budget:{user_id}:{month}"
    global_key = f"budget:global:{month}"

    used = float(client.get(user_key) or 0)
    global_used = float(client.get(global_key) or 0)

    if used + estimated_cost > settings.monthly_budget_usd:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "Monthly budget exceeded",
                "used_usd": round(used, 6),
                "budget_usd": settings.monthly_budget_usd,
                "month": month,
            },
        )

    if global_used + estimated_cost > settings.global_monthly_budget_usd:
        raise HTTPException(
            status_code=503,
            detail="Global monthly budget exhausted. Try again next month.",
        )

    return {
        "month": month,
        "used_usd": round(used, 6),
        "budget_usd": settings.monthly_budget_usd,
        "remaining_usd": round(settings.monthly_budget_usd - used, 6),
    }


def record_usage(user_id: str, input_tokens: int, output_tokens: int) -> dict[str, float | str]:
    cost = estimate_cost(input_tokens, output_tokens)
    client = get_redis()
    month = _month_key()
    user_key = f"budget:{user_id}:{month}"
    global_key = f"budget:global:{month}"

    used = float(client.incrbyfloat(user_key, cost))
    global_used = float(client.incrbyfloat(global_key, cost))
    client.expire(user_key, MONTH_TTL_SECONDS)
    client.expire(global_key, MONTH_TTL_SECONDS)

    return {
        "month": month,
        "cost_usd": round(cost, 6),
        "used_usd": round(used, 6),
        "global_used_usd": round(global_used, 6),
        "budget_usd": settings.monthly_budget_usd,
    }


def get_usage(user_id: str) -> dict[str, float | str]:
    return check_budget(user_id, 0.0)
