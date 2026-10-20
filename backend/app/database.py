"""
backend/app/database.py
========================
Async SQLAlchemy 2.0 setup with aiosqlite.
Models: User, Project, Document, AnalysisResult, PRReviewRun
"""

import enum
from datetime import datetime, timezone
from typing import AsyncGenerator, List, Optional

from sqlalchemy import (
    JSON, DateTime, Enum, ForeignKey, Integer, String, Text, Boolean, UniqueConstraint
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.config import settings

# ---------------------------------------------------------------------------
# Engine & Session Factory
# ---------------------------------------------------------------------------

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {},
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Create all tables. Called on app startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class DocType(str, enum.Enum):
    SRS    = "SRS"
    API_V1 = "API_V1"
    API_V2 = "API_V2"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class User(Base):
    """
    Google OAuth 2.0 authenticated user.
    No password stored — identity is verified via Google ID token.
    """
    __tablename__ = "users"

    id         : Mapped[int]           = mapped_column(Integer, primary_key=True)
    google_id  : Mapped[str]           = mapped_column(String(128), unique=True, nullable=False, index=True)
    email      : Mapped[str]           = mapped_column(String(255), unique=True, nullable=False, index=True)
    name       : Mapped[str]           = mapped_column(String(255), nullable=False)
    avatar_url : Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    is_active  : Mapped[bool]          = mapped_column(Boolean, default=True)
    created_at : Mapped[datetime]      = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    projects: Mapped[List["Project"]] = relationship(
        "Project", back_populates="owner", cascade="all, delete-orphan"
    )


class Project(Base):
    """A named traceability audit project belonging to one user."""
    __tablename__ = "projects"

    id          : Mapped[int]           = mapped_column(Integer, primary_key=True)
    user_id     : Mapped[int]           = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name        : Mapped[str]           = mapped_column(String(255), nullable=False)
    description : Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at  : Mapped[datetime]      = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    owner     : Mapped["User"]              = relationship("User", back_populates="projects")
    documents : Mapped[List["Document"]]    = relationship(
        "Document", back_populates="project", cascade="all, delete-orphan"
    )
    results   : Mapped[List["AnalysisResult"]] = relationship(
        "AnalysisResult", back_populates="project", cascade="all, delete-orphan"
    )


class Document(Base):
    """An uploaded artifact (SRS or OpenAPI spec) scoped to a project."""
    __tablename__ = "documents"

    id                : Mapped[int]           = mapped_column(Integer, primary_key=True)
    project_id        : Mapped[int]           = mapped_column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    doc_type          : Mapped[DocType]       = mapped_column(Enum(DocType), nullable=False)
    file_path         : Mapped[str]           = mapped_column(String(512), nullable=False)
    original_filename : Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    raw_text          : Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    uploaded_at       : Mapped[datetime]      = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    project: Mapped["Project"] = relationship("Project", back_populates="documents")


class AnalysisResult(Base):
    """Cached analysis output: traceability matrix, drift report, IR metrics."""
    __tablename__ = "analysis_results"

    id           : Mapped[int]           = mapped_column(Integer, primary_key=True)
    project_id   : Mapped[int]           = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, unique=True, index=True
    )
    matrix_json  : Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    drift_json   : Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    metrics_json : Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    graph_json   : Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)   # 3D graph nodes+edges
    model_name   : Mapped[Optional[str]]  = mapped_column(String(128), nullable=True)
    created_at   : Mapped[datetime]       = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    project: Mapped["Project"] = relationship("Project", back_populates="results")


class PRReviewRun(Base):
    """One GitHub App review of a pull request head (docs/plan.md §4.4 step 9).

    verdict: PASS | WARN | BLOCK (engine verdicts), SKIPPED (no config / no spec
    change), ERROR (the review could not run). A completed row (anything but
    ERROR) also makes a redelivered webhook a no-op, across restarts."""
    __tablename__ = "pr_review_runs"
    __table_args__ = (UniqueConstraint("delivery_id", "repo", "pr_number", name="uq_review_delivery_pr"),)

    id              : Mapped[int]            = mapped_column(Integer, primary_key=True)
    installation_id : Mapped[int]            = mapped_column(Integer, nullable=False)
    repo            : Mapped[str]            = mapped_column(String(255), nullable=False, index=True)   # owner/name
    pr_number       : Mapped[int]            = mapped_column(Integer, nullable=False)
    pr_title        : Mapped[Optional[str]]  = mapped_column(String(512), nullable=True)
    pr_url          : Mapped[Optional[str]]  = mapped_column(String(512), nullable=True)
    pr_author       : Mapped[Optional[str]]  = mapped_column(String(255), nullable=True)
    head_sha        : Mapped[str]            = mapped_column(String(64), nullable=False)
    base_sha        : Mapped[Optional[str]]  = mapped_column(String(64), nullable=True)
    delivery_id     : Mapped[Optional[str]]  = mapped_column(String(64), nullable=True, index=True)
    trigger         : Mapped[Optional[str]]  = mapped_column(String(64), nullable=True)
    verdict         : Mapped[str]            = mapped_column(String(16), nullable=False)
    conclusion      : Mapped[Optional[str]]  = mapped_column(String(32), nullable=True)   # check-run conclusion
    title           : Mapped[Optional[str]]  = mapped_column(String(255), nullable=True)  # check-run title
    summary         : Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    violations_json : Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    report_json     : Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)   # ReviewResult.to_dict() minus markdown
    markdown        : Mapped[Optional[str]]  = mapped_column(Text, nullable=True)   # the PR comment
    linked_issues   : Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    check_run_id    : Mapped[Optional[int]]  = mapped_column(Integer, nullable=True)
    check_run_url   : Mapped[Optional[str]]  = mapped_column(String(512), nullable=True)
    comment_url     : Mapped[Optional[str]]  = mapped_column(String(512), nullable=True)
    error           : Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    created_at      : Mapped[datetime]       = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
