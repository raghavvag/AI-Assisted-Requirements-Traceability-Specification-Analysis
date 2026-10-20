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

    # Uploads
    UPLOAD_DIR: str = str(_BACKEND_DIR / "uploads")
    MAX_UPLOAD_MB: int = 10                 # per file
    MAX_DOCX_UNCOMPRESSED_MB: int = 50      # zip-bomb guard for .docx

    # GitHub App (PR review bot). See manual.md §D and docs/plan.md §4.
    GITHUB_APP_ID: str = ""                         # numeric App ID from the app's settings page
    GITHUB_APP_PRIVATE_KEY_PATH: str = ""           # path to the downloaded .pem (keep it OUTSIDE the repo)
    GITHUB_APP_PRIVATE_KEY: str = ""                # alternative: the PEM itself (CI / hosting secrets); "\n" escapes allowed
    GITHUB_WEBHOOK_SECRET: str = ""                 # the "Webhook secret" entered when registering the app
    GITHUB_APP_SLUG: str = ""                       # e.g. "traceability-bot" (from the app URL)
    GITHUB_CHECK_NAME: str = "Requirements Traceability"
    GITHUB_API_URL: str = "https://api.github.com"  # GitHub Enterprise: https://<host>/api/v3

    @property
    def github_app_configured(self) -> bool:
        """App ID + a private key + webhook secret are all present."""
        has_key = bool(self.GITHUB_APP_PRIVATE_KEY.strip()) or bool(self.GITHUB_APP_PRIVATE_KEY_PATH.strip())
        return bool(self.GITHUB_APP_ID.strip()) and has_key and bool(self.GITHUB_WEBHOOK_SECRET)


settings = Settings()
