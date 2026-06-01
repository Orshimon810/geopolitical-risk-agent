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

    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    model_name: str = os.getenv("MODEL_NAME", "gpt-4o-mini")
    chroma_dir: str = os.getenv("CHROMA_DIR", ".chroma")
    app_env: str = os.getenv("APP_ENV", "dev")
    session_query_limit: int = int(os.getenv("SESSION_QUERY_LIMIT", "5"))
    daily_query_limit: int = int(os.getenv("DAILY_QUERY_LIMIT", "30"))


# Singleton-like settings object
settings = Settings()
