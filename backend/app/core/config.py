"""
backend/app/core/config.py
==========================
Centralised settings from .env using pydantic-settings.

The .env path is resolved ABSOLUTELY (relative to this file: backend/.env), so
settings load correctly no matter which directory uvicorn is launched from.
A repo-root .env, if present, is read as a lower-priority fallback.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parents[2]        # .../backend
_REPO_ROOT = _BACKEND_DIR.parent                          # repo root

# pydantic-settings: later files in the tuple take precedence, so list the
# repo-root .env first (fallback) and backend/.env last (wins).
_ENV_FILES = (str(_REPO_ROOT / ".env"), str(_BACKEND_DIR / ".env"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    APP_ENV: str = "development"
    SECRET_KEY: str = "change-me-in-production-at-least-32-chars-long"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Google OAuth
    GOOGLE_CLIENT_ID: str = ""

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./traceability.db"

    # Qdrant
    QDRANT_URL: str = "http://localhost:6338"
    QDRANT_COLLECTION: str = "requirements_traceability"

    # LLM — OpenAI is primary, Groq is optional fallback
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    # Embedding
    EMBEDDING_MODEL: str = "all-mpnet-base-v2"
    EMBEDDING_DIM: int = 768

    # CORS
    FRONTEND_URL: str = "http://localhost:5173"


settings = Settings()
