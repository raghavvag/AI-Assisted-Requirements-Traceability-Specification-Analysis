"""
backend/app/routers/projects.py
================================
Project CRUD + file upload endpoints.
All operations enforce user_id ownership (AuthZ).
"""

import logging
import shutil
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.database import AnalysisResult, Document, DocType, Project, User, get_db
from app.schemas import DocumentOut, ProjectCreate, ProjectOut

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["projects"])

UPLOAD_DIR = Path(__file__).parent.parent.parent / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_project_or_404(
    project_id: int, user: User, db: AsyncSession
) -> Project:
    """Fetch project and verify ownership. Raises 404 or 403."""
    result  = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    if project.user_id != user.id:
        raise HTTPException(status_code=403, detail="Access denied.")
    return project


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------

@router.post("", response_model=ProjectOut, status_code=201)
async def create_project(
    body: ProjectCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new traceability project."""
    project = Project(user_id=current_user.id, name=body.name, description=body.description)
    db.add(project)
    await db.flush()
    await db.commit()
    await db.refresh(project)
    logger.info("Created project '%s' for user %d", project.name, current_user.id)
    return ProjectOut(
        id=project.id, name=project.name,
        description=project.description, created_at=project.created_at,
    )


@router.get("", response_model=List[ProjectOut])
async def list_projects(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all projects owned by the current user."""
    result   = await db.execute(
        select(Project).where(Project.user_id == current_user.id)
        .order_by(Project.created_at.desc())
    )
    projects = result.scalars().all()

    out = []
    for p in projects:
        doc_result = await db.execute(select(Document).where(Document.project_id == p.id))
        docs       = doc_result.scalars().all()
        ar_result  = await db.execute(select(AnalysisResult).where(AnalysisResult.project_id == p.id))
        ar         = ar_result.scalar_one_or_none()
        out.append(ProjectOut(
            id=p.id, name=p.name, description=p.description,
            created_at=p.created_at, has_analysis=ar is not None,
            doc_count=len(docs),
        ))
    return out


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project_or_404(project_id, current_user, db)
    doc_result = await db.execute(select(Document).where(Document.project_id == project.id))
    docs = doc_result.scalars().all()
    ar_result = await db.execute(select(AnalysisResult).where(AnalysisResult.project_id == project.id))
    ar = ar_result.scalar_one_or_none()
    return ProjectOut(
        id=project.id, name=project.name, description=project.description,
        created_at=project.created_at, has_analysis=ar is not None, doc_count=len(docs),
    )


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete project + cascaded records + Qdrant vectors."""
    project = await _get_project_or_404(project_id, current_user, db)

    # Delete Qdrant vectors
    from app.services.vector_store import get_vector_store
    vs = get_vector_store()
    vs.delete_project_vectors(user_id=current_user.id, project_id=project_id)

    # Delete upload directory
    proj_dir = UPLOAD_DIR / str(project_id)
    if proj_dir.exists():
        shutil.rmtree(proj_dir)

    await db.delete(project)
    await db.commit()


# ---------------------------------------------------------------------------
# File Upload
# ---------------------------------------------------------------------------

@router.post("/{project_id}/upload", response_model=DocumentOut, status_code=201)
async def upload_document(
    project_id: int,
    doc_type: str = Form(..., description="SRS | API_V1 | API_V2"),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload an SRS or OpenAPI spec file for a project.
    Accepts: .md, .txt, .json, .yaml, .yml
    Performs upsert (one document per doc_type per project).
    """
    # Validate doc_type
    try:
        dtype = DocType[doc_type.upper()]
    except KeyError:
        raise HTTPException(status_code=400, detail=f"Invalid doc_type '{doc_type}'. Use SRS, API_V1, or API_V2.")

    project = await _get_project_or_404(project_id, current_user, db)

    # Validate file extension
    allowed = {".md", ".txt", ".json", ".yaml", ".yml"}
    ext = Path(file.filename).suffix.lower()
    if ext not in allowed:
        raise HTTPException(status_code=400, detail=f"File type '{ext}' not allowed.")

    # Save file
    proj_dir = UPLOAD_DIR / str(project_id)
    proj_dir.mkdir(parents=True, exist_ok=True)
    save_path = proj_dir / f"{dtype.value}{ext}"
    with open(save_path, "wb") as f:
        content = await file.read()
        f.write(content)

    # Read raw text for storage
    try:
        raw_text = content.decode("utf-8", errors="replace")
    except Exception:
        raw_text = None

    # Upsert Document record
    result = await db.execute(
        select(Document).where(
            (Document.project_id == project_id) & (Document.doc_type == dtype)
        )
    )
    doc = result.scalar_one_or_none()

    if doc:
        doc.file_path = str(save_path)
        doc.original_filename = file.filename
        doc.raw_text = raw_text
    else:
        doc = Document(
            project_id=project_id, doc_type=dtype,
            file_path=str(save_path), original_filename=file.filename,
            raw_text=raw_text,
        )
        db.add(doc)

    await db.commit()
    await db.refresh(doc)
    logger.info("Uploaded %s for project %d", dtype.value, project_id)
    return DocumentOut.model_validate(doc)


@router.get("/{project_id}/documents", response_model=List[DocumentOut])
async def list_documents(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_project_or_404(project_id, current_user, db)
    result = await db.execute(select(Document).where(Document.project_id == project_id))
    return [DocumentOut.model_validate(d) for d in result.scalars().all()]
