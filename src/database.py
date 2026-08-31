"""
database.py — SQLAlchemy 2.0 ORM Layer
=======================================
Schema:
  User           → id, username, email, password_hash
  Project        → id, user_id (FK), name, created_at
  Document       → id, project_id (FK), doc_type (Enum), file_path, uploaded_at
  AnalysisState  → id, project_id (FK), traceability_matrix (JSON), drift_report (JSON), computed_at

All models use SQLAlchemy 2.0 DeclarativeBase. The database is SQLite (app.db in project root).
"""

import enum
import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    select,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Engine & Session factory
# ---------------------------------------------------------------------------

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "app.db"
DB_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DB_URL,
    connect_args={"check_same_thread": False},  # Required for SQLite + multithreading
    echo=False,
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """
    Context manager yielding a SQLAlchemy Session.
    Commits on clean exit, rolls back on exception.

    Usage:
        with get_session() as session:
            user = session.scalar(select(User).where(User.username == "alice"))
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class DocType(str, enum.Enum):
    srs    = "srs"
    api_v1 = "api_v1"
    api_v2 = "api_v2"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class User(Base):
    """Registered user with bcrypt-hashed password."""
    __tablename__ = "users"

    id            : Mapped[int]  = mapped_column(Integer, primary_key=True, index=True)
    username      : Mapped[str]  = mapped_column(String(64), unique=True, nullable=False, index=True)
    email         : Mapped[str]  = mapped_column(String(255), unique=True, nullable=False)
    password_hash : Mapped[str]  = mapped_column(String(255), nullable=False)
    is_active     : Mapped[bool] = mapped_column(Boolean, default=True)
    created_at    : Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    projects: Mapped[List["Project"]] = relationship(
        "Project", back_populates="owner", cascade="all, delete-orphan"
    )


class Project(Base):
    """A named traceability analysis project belonging to one user."""
    __tablename__ = "projects"

    id         : Mapped[int]  = mapped_column(Integer, primary_key=True, index=True)
    user_id    : Mapped[int]  = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name       : Mapped[str]  = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at : Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    owner     : Mapped["User"]          = relationship("User", back_populates="projects")
    documents : Mapped[List["Document"]] = relationship(
        "Document", back_populates="project", cascade="all, delete-orphan"
    )
    analysis  : Mapped[Optional["AnalysisState"]] = relationship(
        "AnalysisState", back_populates="project", uselist=False, cascade="all, delete-orphan"
    )


class Document(Base):
    """An uploaded artefact (SRS or OpenAPI spec) associated with a project."""
    __tablename__ = "documents"

    id          : Mapped[int]  = mapped_column(Integer, primary_key=True, index=True)
    project_id  : Mapped[int]  = mapped_column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    doc_type    : Mapped[DocType] = mapped_column(Enum(DocType), nullable=False)
    file_path   : Mapped[str]  = mapped_column(String(512), nullable=False)
    original_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    uploaded_at : Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="documents")


class AnalysisState(Base):
    """
    Cached analysis results for a project.
    Stores the full traceability matrix and drift report as JSON blobs
    so the UI can resume without re-embedding.
    """
    __tablename__ = "analysis_states"

    id                   : Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id           : Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id"), nullable=False, unique=True, index=True
    )
    traceability_matrix  : Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    drift_report         : Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    compliance_summary   : Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    embedding_model      : Mapped[Optional[str]]  = mapped_column(String(128), nullable=True)
    computed_at          : Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="analysis")


# ---------------------------------------------------------------------------
# DB initialisation
# ---------------------------------------------------------------------------

def init_db() -> None:
    """
    Create all tables if they don't already exist.
    Safe to call multiple times (idempotent).
    """
    Base.metadata.create_all(bind=engine)
    logger.info("Database initialised at: %s", DB_PATH)


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------

# ── User ─────────────────────────────────────────────────────────────────────

def create_user(username: str, email: str, password_hash: str) -> User:
    """Create and persist a new User. Raises ValueError if username/email taken."""
    with get_session() as session:
        existing = session.scalar(
            select(User).where(
                (User.username == username) | (User.email == email)
            )
        )
        if existing:
            if existing.username == username:
                raise ValueError(f"Username '{username}' is already taken.")
            raise ValueError(f"Email '{email}' is already registered.")

        user = User(username=username, email=email, password_hash=password_hash)
        session.add(user)
        session.flush()
        # Detach from session before returning (avoid lazy-load outside session)
        session.expunge(user)
        return user


def get_user_by_username(username: str) -> Optional[User]:
    """Return a User by username, or None if not found."""
    with get_session() as session:
        user = session.scalar(select(User).where(User.username == username))
        if user:
            session.expunge(user)
        return user


def get_user_by_id(user_id: int) -> Optional[User]:
    """Return a User by primary key, or None."""
    with get_session() as session:
        user = session.get(User, user_id)
        if user:
            session.expunge(user)
        return user


# ── Project ──────────────────────────────────────────────────────────────────

def create_project(user_id: int, name: str, description: str = "") -> Project:
    """Create a new Project for a user."""
    with get_session() as session:
        project = Project(user_id=user_id, name=name, description=description)
        session.add(project)
        session.flush()
        session.expunge(project)
        return project


def list_projects(user_id: int) -> List[Project]:
    """Return all Projects owned by the given user, newest first."""
    with get_session() as session:
        projects = session.scalars(
            select(Project)
            .where(Project.user_id == user_id)
            .order_by(Project.created_at.desc())
        ).all()
        for p in projects:
            session.expunge(p)
        return list(projects)


def get_project(project_id: int, user_id: int) -> Optional[Project]:
    """
    Return a Project by ID, enforcing ownership.
    Returns None if the project doesn't exist or belongs to a different user.
    """
    with get_session() as session:
        project = session.scalar(
            select(Project).where(
                (Project.id == project_id) & (Project.user_id == user_id)
            )
        )
        if project:
            session.expunge(project)
        return project


def delete_project(project_id: int, user_id: int) -> bool:
    """Delete a project and all its cascaded records. Returns True on success."""
    with get_session() as session:
        project = session.scalar(
            select(Project).where(
                (Project.id == project_id) & (Project.user_id == user_id)
            )
        )
        if not project:
            return False
        session.delete(project)
        return True


# ── Document ─────────────────────────────────────────────────────────────────

def save_document(
    project_id: int,
    doc_type: DocType,
    file_path: str,
    original_filename: str = "",
) -> Document:
    """Upsert a Document record (one per doc_type per project)."""
    with get_session() as session:
        existing = session.scalar(
            select(Document).where(
                (Document.project_id == project_id) &
                (Document.doc_type == doc_type)
            )
        )
        if existing:
            existing.file_path = file_path
            existing.original_filename = original_filename
            existing.uploaded_at = datetime.now(timezone.utc)
            session.flush()
            session.expunge(existing)
            return existing
        doc = Document(
            project_id=project_id,
            doc_type=doc_type,
            file_path=file_path,
            original_filename=original_filename,
        )
        session.add(doc)
        session.flush()
        session.expunge(doc)
        return doc


def get_documents(project_id: int) -> List[Document]:
    """Return all Documents for a project."""
    with get_session() as session:
        docs = session.scalars(
            select(Document).where(Document.project_id == project_id)
        ).all()
        for d in docs:
            session.expunge(d)
        return list(docs)


# ── AnalysisState ─────────────────────────────────────────────────────────────

def save_analysis_state(
    project_id: int,
    matrix_df,             # pd.DataFrame
    drift_df=None,         # pd.DataFrame | None
    summary_df=None,       # pd.DataFrame | None
    embedding_model: str = "",
) -> AnalysisState:
    """
    Persist analysis results for a project.
    Converts DataFrames to JSON-serialisable dicts.
    """
    matrix_json  = matrix_df.to_dict() if matrix_df is not None else None
    drift_json   = drift_df.to_dict(orient="records") if drift_df is not None else None
    summary_json = summary_df.to_dict() if summary_df is not None else None

    with get_session() as session:
        existing = session.scalar(
            select(AnalysisState).where(AnalysisState.project_id == project_id)
        )
        if existing:
            existing.traceability_matrix = matrix_json
            existing.drift_report        = drift_json
            existing.compliance_summary  = summary_json
            existing.embedding_model     = embedding_model
            existing.computed_at         = datetime.now(timezone.utc)
            session.flush()
            session.expunge(existing)
            return existing

        state = AnalysisState(
            project_id=project_id,
            traceability_matrix=matrix_json,
            drift_report=drift_json,
            compliance_summary=summary_json,
            embedding_model=embedding_model,
        )
        session.add(state)
        session.flush()
        session.expunge(state)
        return state


def load_analysis_state(project_id: int) -> Optional[AnalysisState]:
    """Load the most recent AnalysisState for a project, or None."""
    with get_session() as session:
        state = session.scalar(
            select(AnalysisState).where(AnalysisState.project_id == project_id)
        )
        if state:
            session.expunge(state)
        return state
