"""
backend/app/routers/analysis.py
=================================
Analysis pipeline endpoints:
  POST /api/projects/{id}/analyze       — trigger full pipeline
  GET  /api/projects/{id}/matrix        — N×M matrix + 3D graph payload
  GET  /api/projects/{id}/drift         — version drift deltas
  POST /api/projects/{id}/pr-review     — PR compliance simulation
  GET  /api/projects/{id}/benchmark     — IR evaluation metrics
  GET  /api/projects/{id}/explain/{req_id} — LLM root-cause for a requirement
"""

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.database import AnalysisResult, Document, DocType, User, get_db
from app.routers.projects import _get_project_or_404
from app.schemas import AnalyzeRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["analysis"])

ROOT = Path(__file__).parent.parent.parent.parent
DEFAULT_GT_PATH = ROOT / "data" / "ground_truth" / "traceability_labels.csv"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_analysis_or_404(project_id: int, db: AsyncSession) -> AnalysisResult:
    result = await db.execute(select(AnalysisResult).where(AnalysisResult.project_id == project_id))
    ar = result.scalar_one_or_none()
    if ar is None:
        raise HTTPException(status_code=404, detail="Analysis not found. Run /analyze first.")
    return ar


async def _get_doc_path(project_id: int, doc_type: DocType, db: AsyncSession) -> Optional[str]:
    result = await db.execute(
        select(Document).where(
            (Document.project_id == project_id) & (Document.doc_type == doc_type)
        )
    )
    doc = result.scalar_one_or_none()
    return doc.file_path if doc else None


# ---------------------------------------------------------------------------
# POST /analyze
# ---------------------------------------------------------------------------

@router.post("/{project_id}/analyze", summary="Trigger full analysis pipeline")
async def analyze_project(
    project_id: int,
    body: AnalyzeRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Orchestrates: Parse → Embed → Qdrant upsert → Matrix → Drift → Graph.
    Runs synchronously (use background_tasks for async in production).
    """
    await _get_project_or_404(project_id, current_user, db)

    srs_path = await _get_doc_path(project_id, DocType.SRS, db)
    v1_path  = await _get_doc_path(project_id, DocType.API_V1, db)
    v2_path  = await _get_doc_path(project_id, DocType.API_V2, db) if body.enable_drift else None

    # Use built-in synthetic data if project has no uploads yet
    if not srs_path:
        srs_path = str(ROOT / "data" / "requirements" / "ecommerce_srs.md")
    if not v1_path:
        v1_path = str(ROOT / "data" / "api_specs" / "store_v1.json")
    if body.enable_drift and not v2_path:
        v2_path = str(ROOT / "data" / "api_specs" / "store_v2.json")

    gt_path = str(DEFAULT_GT_PATH) if DEFAULT_GT_PATH.exists() else None

    from app.services.pipeline import run_analysis_pipeline
    try:
        result_data = await run_analysis_pipeline(
            project_id=project_id,
            user_id=current_user.id,
            srs_path=srs_path,
            v1_path=v1_path,
            v2_path=v2_path,
            threshold=body.threshold,
            drift_threshold=body.drift_threshold,
            gt_path=gt_path,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=f"An uploaded file is missing; upload it again. ({e})")
    except ValueError as e:
        # Unusable input (no requirements in the SRS, spec without paths, ...):
        # the user can fix this, so it is a 422, not a server error.
        logger.warning("Analysis input rejected for project %d: %s", project_id, e)
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("Pipeline failed for project %d", project_id)
        raise HTTPException(status_code=500, detail=f"Pipeline error: {e}")

    # Persist / upsert AnalysisResult
    ar_result = await db.execute(select(AnalysisResult).where(AnalysisResult.project_id == project_id))
    ar = ar_result.scalar_one_or_none()

    from app.services.pipeline import get_embedder
    embedder = get_embedder()

    if ar:
        ar.matrix_json  = result_data["matrix_json"]
        ar.drift_json   = result_data["drift_json"]
        ar.metrics_json = result_data["metrics_json"]
        ar.graph_json   = result_data["graph_json"]
        ar.model_name   = embedder.model_name
    else:
        ar = AnalysisResult(
            project_id=project_id,
            matrix_json=result_data["matrix_json"],
            drift_json=result_data["drift_json"],
            metrics_json=result_data["metrics_json"],
            graph_json=result_data["graph_json"],
            model_name=embedder.model_name,
        )
        db.add(ar)

    await db.commit()

    matrix = result_data["matrix_json"]
    return {
        "status": "ok",
        "overall_compliance": matrix.get("overall_compliance"),
        "n_requirements":     len(matrix.get("requirements", [])),
        "n_endpoints":        len(matrix.get("endpoints", [])),
        "n_drifted":          result_data["drift_json"].get("n_drifted", 0),
    }


# ---------------------------------------------------------------------------
# GET /matrix
# ---------------------------------------------------------------------------

@router.get("/{project_id}/matrix", summary="Get traceability matrix and 3D graph payload")
async def get_matrix(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_project_or_404(project_id, current_user, db)
    ar = await _get_analysis_or_404(project_id, db)
    return {
        **ar.matrix_json,
        "graph": ar.graph_json,
    }


# ---------------------------------------------------------------------------
# GET /drift
# ---------------------------------------------------------------------------

@router.get("/{project_id}/drift", summary="Get version drift analysis")
async def get_drift(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_project_or_404(project_id, current_user, db)
    ar = await _get_analysis_or_404(project_id, db)

    drift = ar.drift_json or {}
    if not drift:
        return {"message": "No drift data. Re-run analyze with enable_drift=true and an API v2 spec."}
    return drift


# ---------------------------------------------------------------------------
# POST /pr-review
# ---------------------------------------------------------------------------

@router.post("/{project_id}/pr-review", summary="Simulate PR compliance review")
async def pr_review(
    project_id: int,
    scenario: str = Query(
        default="remove_idempotency",
        description="PR scenario key: add_endpoint | remove_idempotency | drop_2fa_requirement",
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Runs a built-in PR scenario through the shared review engine (src/review)."""
    import sys
    sys.path.insert(0, str(ROOT))
    from src.pr_reviewer import PRReviewer, resolve_scenario_key
    from src.explainer import ComplianceExplainer
    from app.services.pipeline import get_embedder

    await _get_project_or_404(project_id, current_user, db)
    ar = await _get_analysis_or_404(project_id, db)

    matrix_data  = ar.matrix_json or {}
    requirements = matrix_data.get("requirements", [])
    if not requirements:
        raise HTTPException(status_code=400, detail="No requirements in analysis. Re-run analyze.")

    embedder       = get_embedder()
    req_embeddings = embedder.encode_requirements(requirements)
    explainer      = ComplianceExplainer()  # safe even with no API key (template fallback)

    reviewer = PRReviewer(embedder=embedder, explainer=explainer)
    resolved = resolve_scenario_key(scenario)
    result = reviewer.review(resolved, requirements, req_embeddings)
    return {
        "scenario":       resolved,
        "verdict":        result.verdict.value,          # PASS | WARN | BLOCK
        "approved":       result.approved,
        "summary":        result.summary,
        "violations":     [v.to_dict() for v in result.violations],
        "changes":        [c.to_dict() for c in result.diff.changes],
        "review_comment": result.markdown,
    }


# ---------------------------------------------------------------------------
# GET /benchmark
# ---------------------------------------------------------------------------

@router.get("/{project_id}/benchmark", summary="IR evaluation metrics")
async def get_benchmark(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_project_or_404(project_id, current_user, db)
    ar = await _get_analysis_or_404(project_id, db)

    metrics = ar.metrics_json or {}
    if not metrics:
        return {"message": "Metrics not available. Ensure traceability_labels.csv exists and re-run analyze."}
    return metrics


# ---------------------------------------------------------------------------
# GET /explain/{req_id}
# ---------------------------------------------------------------------------

@router.get("/{project_id}/explain/{req_id}", summary="LLM explanation for a requirement")
async def explain_requirement(
    project_id: int,
    req_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate an AI root-cause explanation for a specific requirement's compliance status."""
    import sys
    sys.path.insert(0, str(ROOT))
    from src.explainer import ComplianceExplainer

    await _get_project_or_404(project_id, current_user, db)
    ar = await _get_analysis_or_404(project_id, db)

    matrix_data  = ar.matrix_json or {}
    requirements = matrix_data.get("requirements", [])
    endpoints    = matrix_data.get("endpoints", [])
    cells        = matrix_data.get("cells", [])

    req = next((r for r in requirements if r["id"] == req_id), None)
    if not req:
        raise HTTPException(status_code=404, detail=f"Requirement '{req_id}' not found.")

    req_cells = [c for c in cells if c["req_id"] == req_id]
    if not req_cells:
        raise HTTPException(status_code=404, detail="No matrix cells for this requirement.")

    best = max(req_cells, key=lambda c: c["score"])
    best_ep = next((e for e in endpoints if e.get("id") == best["ep_id"]), {})

    try:
        explainer = ComplianceExplainer()
        explanation = explainer.explain_compliance(
            requirement=req,
            endpoint_id=best["ep_id"],
            score=best["score"],
            category=best["category"],
            endpoint_text=best_ep.get("text", ""),
        )
    except Exception as e:  # noqa: BLE001
        explanation = f"LLM explanation unavailable: {e}"

    return {
        "requirement_id":    req_id,
        "requirement_title": req.get("title", req_id),
        "best_endpoint":     best["ep_id"],
        "best_score":        best["score"],
        "category":          best["category"],
        "explanation":       explanation,
    }
