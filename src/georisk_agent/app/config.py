from dotenv import load_dotenv
from pydantic import BaseModel
import os

load_dotenv()

_WEAK_JWT_DEFAULT = "change-me-in-production"


class Settings(BaseModel):
    """Centralised application configuration. All values sourced from environment variables."""

    # --- Core ---
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    model_name: str = os.getenv("MODEL_NAME", "gpt-4o-mini")
    app_env: str = os.getenv("APP_ENV", "dev")

    # --- PostgreSQL / pgvector ---
    database_url: str = os.getenv("DATABASE_URL", "")

    # --- JWT ---
    jwt_secret_key: str = os.getenv("JWT_SECRET_KEY", _WEAK_JWT_DEFAULT)
    jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
    access_token_expire_minutes: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

    # --- Redis ---
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    rate_limit_per_hour: int = int(os.getenv("RATE_LIMIT_PER_HOUR", "5"))

    # --- Celery ---
    broker_url: str = os.getenv("BROKER_URL", "redis://localhost:6379/1")
    result_backend: str = os.getenv("RESULT_BACKEND", "redis://localhost:6379/2")

    # --- Query result cache ---
    query_cache_ttl_seconds: int = int(os.getenv("QUERY_CACHE_TTL", "7200"))

    # --- Refresh tokens ---
    refresh_token_expire_days: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

    # --- Email (password reset via Resend API — https://resend.com) ---
    # Leave RESEND_API_KEY empty to use dev mode: reset links are logged instead of sent.
    resend_api_key: str = os.getenv("RESEND_API_KEY", "")
    smtp_from: str = os.getenv("SMTP_FROM", "onboarding@resend.dev")
    frontend_url: str = os.getenv("FRONTEND_URL", "http://localhost:3000")

    # --- Ephemeral news cache ---
    # Provider: "newsapi" (newsapi.org) or "finnhub" (finnhub.io)
    news_provider: str = os.getenv("NEWS_PROVIDER", "newsapi")
    newsapi_key: str = os.getenv("NEWSAPI_KEY", "")
    finnhub_api_key: str = os.getenv("FINNHUB_API_KEY", "")
    # How long (hours) a news article lives in ephemeral_embeddings before being flushed
    ephemeral_ttl_hours: int = int(os.getenv("EPHEMERAL_TTL_HOURS", "48"))

    # --- Dynamic pipeline (Reviewer loop + HITL) ---
    # Max reviewer retries per analysis run (0 = reviewer runs but never retries)
    max_retries: int = int(os.getenv("MAX_RETRIES", "1"))
    # Redis URL for LangGraph checkpoints. Defaults to REDIS_URL (DB 0) so single-DB
    # providers (Upstash free tier) work out of the box. RedisSaver uses "checkpoint:"
    # and "checkpoint_write:" key prefixes that don't collide with other app keys.
    langgraph_redis_url: str = os.getenv(
        "LANGGRAPH_REDIS_URL",
        os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    )
    # Minutes before a WAITING_FOR_INPUT task is auto-approved with the original plan
    hitl_timeout_minutes: int = int(os.getenv("HITL_TIMEOUT_MINUTES", "10"))

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"

    def validate_for_production(self) -> None:
        """
        Fail fast if required env vars are missing or left at insecure defaults.
        Call this once at application startup (lifespan hook in api/main.py).
        In non-prod environments this is a no-op so local dev stays frictionless.
        """
        if not self.is_prod:
            return

        errors: list[str] = []

        if not self.openai_api_key:
            errors.append("OPENAI_API_KEY is not set")

        if not self.database_url:
            errors.append("DATABASE_URL is not set")

        if not self.jwt_secret_key or self.jwt_secret_key == _WEAK_JWT_DEFAULT:
            errors.append(
                "JWT_SECRET_KEY is missing or still set to the insecure default. "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )

        if errors:
            raise RuntimeError(
                "Production startup validation failed — fix these before deploying:\n"
                + "\n".join(f"  • {e}" for e in errors)
            )


settings = Settings()
