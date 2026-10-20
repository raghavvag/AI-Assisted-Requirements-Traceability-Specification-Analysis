"""
backend/app/routers/projects.py
================================
Project CRUD + file upload endpoints.
All operations enforce user_id ownership (AuthZ).
"""

import logging
import os
import shutil
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import get_current_user
from app.database import AnalysisResult, Document, DocType, Project, User, get_db
from app.schemas import (
    DocumentOut, DocumentUploadOut, ExtractionSummary, ProjectCreate, ProjectOut, SrsRequirementsOut,
)
from app.services import srs_ingest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["projects"])

UPLOAD_DIR = Path(settings.UPLOAD_DIR)
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

@router.post("/{project_id}/upload", response_model=DocumentUploadOut, status_code=201)
async def upload_document(
    project_id: int,
    doc_type: str = Form(..., description="SRS | API_V1 | API_V2"),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload an SRS or OpenAPI spec file for a project (one document per doc_type).

    SRS:     .docx, .pdf, .md, .txt. The requirements are extracted immediately;
             a document with no recognisable requirements is rejected with 422
             and an extraction report explaining what was seen.
    API_V*:  .json, .yaml, .yml, checked for an OpenAPI 'paths' section.
    """
    try:
        dtype = DocType[doc_type.upper()]
    except KeyError:
        raise HTTPException(status_code=400, detail=f"Invalid doc_type '{doc_type}'. Use SRS, API_V1, or API_V2.")

    await _get_project_or_404(project_id, current_user, db)

    # Read at most limit+1 bytes so an oversized upload is not held in memory.
    content = await file.read(srs_ingest.max_upload_bytes() + 1)
    ext = srs_ingest.validate_upload(dtype, file.filename or "", content)

    proj_dir = UPLOAD_DIR / str(project_id)
    proj_dir.mkdir(parents=True, exist_ok=True)
    save_path = proj_dir / f"{dtype.value}{ext}"
    tmp_path = proj_dir / f".{dtype.value}.upload{ext}"
    tmp_path.write_bytes(content)

    extraction = None
    try:
        if dtype is DocType.SRS:
            reqs, report = await run_in_threadpool(
                srs_ingest.parse_srs, tmp_path, file.filename or save_path.name
            )
            raw_text = srs_ingest.dump_extraction(reqs, report)
            extraction = srs_ingest.summary_of(reqs, report)
        else:
            raw_text = content.decode("utf-8-sig", errors="replace")
        os.replace(tmp_path, save_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    # A re-upload with a different extension must not leave the old file behind.
    for stale in proj_dir.glob(f"{dtype.value}.*"):
        if stale != save_path:
            stale.unlink(missing_ok=True)

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
    logger.info("Uploaded %s (%s) for project %d", dtype.value, ext, project_id)
    out = DocumentUploadOut.model_validate(doc)
    out.extraction = ExtractionSummary(**extraction) if extraction else None
    return out


@router.get("/{project_id}/srs/requirements", response_model=SrsRequirementsOut)
async def get_srs_requirements(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    The requirements extracted from the project's SRS, with the extraction
    report, for review before analysis. Falls back to the built-in demo SRS
    (is_default = true) when nothing has been uploaded, mirroring /analyze.
    """
    await _get_project_or_404(project_id, current_user, db)
    result = await db.execute(
        select(Document).where((Document.project_id == project_id) & (Document.doc_type == DocType.SRS))
    )
    doc = result.scalar_one_or_none()

    if doc is None:
        reqs, report = await run_in_threadpool(
            srs_ingest.parse_srs, srs_ingest.DEFAULT_SRS, srs_ingest.DEFAULT_SRS.name
        )
        return SrsRequirementsOut(filename=srs_ingest.DEFAULT_SRS.name, is_default=True,
                                  requirements=reqs, report=report)

    cached = srs_ingest.load_extraction(doc.raw_text)
    if cached is None:
        # Uploaded before extraction was stored (older .md uploads): parse now and cache.
        path = Path(doc.file_path)
        if not path.exists():
            raise HTTPException(status_code=404, detail="The uploaded SRS file is missing; upload it again.")
        reqs, report = await run_in_threadpool(srs_ingest.parse_srs, path, doc.original_filename or path.name)
        doc.raw_text = srs_ingest.dump_extraction(reqs, report)
        await db.commit()
    else:
        reqs, report = cached
    return SrsRequirementsOut(filename=doc.original_filename or Path(doc.file_path).name,
                              requirements=reqs, report=report)


@router.get("/{project_id}/documents", response_model=List[DocumentOut])
async def list_documents(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_project_or_404(project_id, current_user, db)
    result = await db.execute(select(Document).where(Document.project_id == project_id))
    return [DocumentOut.model_validate(d) for d in result.scalars().all()]
