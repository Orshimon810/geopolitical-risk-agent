from pydantic import BaseModel
from dotenv import load_dotenv
import os

# Load environment variables from .env if present
load_dotenv()


class Settings(BaseModel):
    """
    Centralized application configuration.
    All environment-dependent values live here.
    """

    # --- Core ---
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    model_name: str = os.getenv("MODEL_NAME", "gpt-4o-mini")
    chroma_dir: str = os.getenv("CHROMA_DIR", ".chroma")
    app_env: str = os.getenv("APP_ENV", "dev")
    session_query_limit: int = int(os.getenv("SESSION_QUERY_LIMIT", "5"))
    daily_query_limit: int = int(os.getenv("DAILY_QUERY_LIMIT", "30"))

    # --- PostgreSQL / pgvector (Step 1) ---
    # Format: postgresql+asyncpg://user:pass@host:5432/dbname
    # Managed services (Neon/Supabase): append ?sslmode=require
    database_url: str = os.getenv("DATABASE_URL", "")

    # --- JWT (Step 2) ---
    # Generate a strong secret with:
    #   python -c "import secrets; print(secrets.token_hex(32))"
    jwt_secret_key: str = os.getenv("JWT_SECRET_KEY", "change-me-in-production")
    jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
    access_token_expire_minutes: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

    # --- Redis (Step 2) ---
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    rate_limit_per_hour: int = int(os.getenv("RATE_LIMIT_PER_HOUR", "5"))

    # --- Celery task queue (Step 2) ---
    # Broker options:
    #   RabbitMQ  → amqp://user:pass@host:5672//
    #   AWS SQS   → sqs://aws_access_key:aws_secret_key@
    #   Redis     → redis://localhost:6379/1  (dev default)
    broker_url: str = os.getenv("BROKER_URL", "redis://localhost:6379/1")
    result_backend: str = os.getenv("RESULT_BACKEND", "redis://localhost:6379/2")


# Singleton-like settings object
settings = Settings()
