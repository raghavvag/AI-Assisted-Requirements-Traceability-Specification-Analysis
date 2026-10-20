"""
backend/app/main.py
=====================
FastAPI application entry point.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.database import init_db
from app.routers import auth, projects, analysis, github
from app.services.github import GitHubAppAuth, GitHubAppConfigError
from app.services.github.review_flow import ReviewFlow
from app.services.vector_store import get_vector_store

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB tables and Qdrant collection."""
    logger.info("Starting AI Traceability Engine API...")

    # Init SQLite tables
    await init_db()
    logger.info("Database tables initialised.")

    # Loud config check — the #1 cause of login failures is an unloaded .env
    gcid = settings.GOOGLE_CLIENT_ID or ""
    if not gcid or "your-project" in gcid:
        logger.error(
            "GOOGLE_CLIENT_ID is NOT loaded (value=%r). Google login WILL fail with "
            "'expected one of [\"\"]'. Fix backend/.env and fully restart uvicorn.", gcid,
        )
    else:
        logger.info("GOOGLE_CLIENT_ID loaded: ...%s", gcid[-24:])
    github_auth = None
    if settings.github_app_configured:
        try:
            github_auth = GitHubAppAuth.from_settings(settings)
            github.set_review_handler(ReviewFlow(github_auth))
            logger.info("GitHub App configured (app id %s); webhook at /api/github/webhook", settings.GITHUB_APP_ID)
        except GitHubAppConfigError as exc:
            logger.error("GitHub App settings are present but unusable: %s. PR reviews are disabled.", exc)
    else:
        logger.info("GitHub App not configured; /api/github/webhook will answer 503 (see manual.md section D).")

    # Init Qdrant collection
    vs = get_vector_store()
    if vs.is_available:
        vs.ensure_collection(dim=settings.EMBEDDING_DIM)
        logger.info("Qdrant collection ready.")
    else:
        logger.warning("Qdrant unavailable at startup — will use NumPy fallback.")

    yield

    logger.info("Shutting down...")
    if github_auth is not None:
        await github_auth.aclose()


app = FastAPI(
    title="AI Requirements Traceability Engine",
    description=(
        "Enterprise-grade semantic compliance verification between "
        "business requirements and OpenAPI specifications. "
        "Multi-tenant, Qdrant-backed, Google OAuth 2.0 secured."
    ),
    version="2.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS — allow the Vite dev server
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL, "http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(analysis.router)
app.include_router(github.router)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/api/health", tags=["health"])
async def health():
    vs    = get_vector_store()
    stats = vs.get_stats()
    gcid  = settings.GOOGLE_CLIENT_ID or ""
    return {
        "status": "ok",
        "qdrant": stats,
        "version": "2.0.0",
        "config": {
            # Masked — just enough to confirm the value is actually loaded.
            "google_client_id_loaded": bool(gcid) and "your-project" not in gcid,
            "google_client_id_suffix": gcid[-24:] if gcid else None,
            "openai_key_loaded": bool(settings.OPENAI_API_KEY)
            and not settings.OPENAI_API_KEY.startswith("sk-your"),
            "llm_provider": (
                "openai" if settings.OPENAI_API_KEY and not settings.OPENAI_API_KEY.startswith("sk-your")
                else "groq" if settings.GROQ_API_KEY
                else "template-fallback"
            ),
            "github_app_configured": settings.github_app_configured,
            "github_app_id_loaded": bool(settings.GITHUB_APP_ID.strip()),
            "github_webhook_secret_loaded": bool(settings.GITHUB_WEBHOOK_SECRET),
        },
    }
