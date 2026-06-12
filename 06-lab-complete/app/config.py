"""12-factor configuration for the production agent."""
import logging
import os
from dataclasses import dataclass, field


def _bool_env(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _list_env(name: str, default: str = "*") -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass
class Settings:
    host: str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "8000")))
    environment: str = field(
        default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    debug: bool = field(default_factory=lambda: _bool_env("DEBUG"))

    app_name: str = field(default_factory=lambda: os.getenv(
        "APP_NAME", "Production AI Agent"))
    app_version: str = field(
        default_factory=lambda: os.getenv("APP_VERSION", "1.0.0"))
    instance_id: str = field(default_factory=lambda: os.getenv(
        "INSTANCE_ID", os.getenv("HOSTNAME", "local")))

    openai_api_key: str = field(
        default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    llm_model: str = field(
        default_factory=lambda: os.getenv("LLM_MODEL", "mock-llm"))

    agent_api_key: str = field(default_factory=lambda: os.getenv(
        "AGENT_API_KEY", "dev-key-change-me"))
    allowed_origins: list[str] = field(
        default_factory=lambda: _list_env("ALLOWED_ORIGINS", "*"))

    rate_limit_per_minute: int = field(
        default_factory=lambda: int(os.getenv("RATE_LIMIT_PER_MINUTE", "10"))
    )
    monthly_budget_usd: float = field(
        default_factory=lambda: float(os.getenv("MONTHLY_BUDGET_USD", "10.0"))
    )
    global_monthly_budget_usd: float = field(
        default_factory=lambda: float(
            os.getenv("GLOBAL_MONTHLY_BUDGET_USD", "100.0"))
    )

    redis_url: str = field(default_factory=lambda: os.getenv(
        "REDIS_URL", "redis://localhost:6379/0"))
    redis_required: bool = field(
        default_factory=lambda: _bool_env("REDIS_REQUIRED", "false"))
    session_ttl_seconds: int = field(
        default_factory=lambda: int(os.getenv("SESSION_TTL_SECONDS", "86400"))
    )
    max_history_messages: int = field(
        default_factory=lambda: int(os.getenv("MAX_HISTORY_MESSAGES", "20"))
    )

    graceful_shutdown_timeout_seconds: int = field(
        default_factory=lambda: int(
            os.getenv("GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS", "30"))
    )

    def validate(self) -> "Settings":
        logger = logging.getLogger(__name__)
        if self.environment == "production" and self.agent_api_key == "dev-key-change-me":
            raise ValueError("AGENT_API_KEY must be set in production")
        if not self.openai_api_key:
            logger.warning("OPENAI_API_KEY is not set; using mock LLM")
        if self.rate_limit_per_minute <= 0:
            raise ValueError("RATE_LIMIT_PER_MINUTE must be positive")
        if self.monthly_budget_usd <= 0:
            raise ValueError("MONTHLY_BUDGET_USD must be positive")
        return self


settings = Settings().validate()
