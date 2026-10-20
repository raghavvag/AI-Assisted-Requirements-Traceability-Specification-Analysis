"""
backend/app/services/pipeline.py
==================================
Orchestrates the full analysis pipeline:
  Parse → Embed → Qdrant upsert → Matrix → Drift → Graph payload
Reuses the existing src/ modules.
"""

import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

# Bring in the existing src/ modules
ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(ROOT))

from src.parser import extract_requirements, extract_endpoints
from src.embedder import SemanticEmbedder
from src.similarity import (
    compute_similarity_matrix,
    build_traceability_matrix,
    compute_overall_compliance_score,
    get_compliance_summary,
    get_compliance_category,
)
from src.drift_detector import (
    compute_drift_report,
    get_drifted_pairs,
    get_compliance_kpis,
)
from src.evaluator import (
    load_ground_truth,
    generate_evaluation_report,
    get_per_requirement_metrics,
    compute_tfidf_similarity_matrix,
    compute_map,
)
from app.services.vector_store import get_vector_store

logger = logging.getLogger(__name__)

# Singleton embedder (loaded once per process)
_embedder: Optional[SemanticEmbedder] = None


def get_embedder() -> SemanticEmbedder:
    global _embedder
    if _embedder is None:
        _embedder = SemanticEmbedder()
    return _embedder


# ---------------------------------------------------------------------------
# Graph payload builder (for 3D force graph)
# ---------------------------------------------------------------------------

def build_graph_payload(
    requirements: List[Dict],
    endpoints_v1: List[Dict],
    endpoints_v2: Optional[List[Dict]],
    matrix_df: pd.DataFrame,
    drift_df: Optional[pd.DataFrame],
    threshold: float = 0.55,
) -> Tuple[List[Dict], List[Dict]]:
    """
    Build nodes and edges for the react-force-graph-3d component.

    Node types:
      - requirement: glowing blue sphere
      - endpoint_v1: green cube (compliant)
      - endpoint_v2_drifted: pulsing red octahedron
      - endpoint_v2_stable: green cube

    Edge weight: cosine similarity score
    """
    nodes, edges = [], []

    # Requirement nodes
    for req in requirements:
        nodes.append({
            "id": req["id"],
            "label": req["id"],
            "title": req.get("title", req["id"]),
            "type": "requirement",
            "color": "#3B82F6",     # blue
            "size": 8,
        })

    # Endpoint v1 nodes
    drifted_ids = set()
    if drift_df is not None:
        drifted_ids = set(drift_df[drift_df["drifted"] == True]["endpoint_id"].tolist())  # noqa: E712 (pandas mask)

    for ep in endpoints_v1:
        ep_id = ep["id"]
        is_drifted = ep_id in drifted_ids
        nodes.append({
            "id": ep_id,
            "label": f"{ep.get('method','')} {ep.get('path','')}",
            "type": "endpoint_v1_drifted" if is_drifted else "endpoint_v1",
            "color": "#EF4444" if is_drifted else "#22C55E",  # red or green
            "size": 6,
        })

    # v2 endpoint nodes (if drift analysis run)
    if endpoints_v2:
        for ep in endpoints_v2:
            ep_id_v2 = ep["id"]
            # Check if this endpoint exists in v1 (via path normalisation)
            method = ep.get("method","")
            path   = ep.get("path","").replace("/api/v2/", "/api/v1/")
            v1_id  = f"{method} {path}".strip()
            if v1_id not in {e["id"] for e in endpoints_v1}:
                nodes.append({
                    "id": ep_id_v2,
                    "label": f"{method} {ep.get('path','')}",
                    "type": "endpoint_v2_new",
                    "color": "#F59E0B",  # amber — new/unknown
                    "size": 6,
                })

    # Edges from matrix
    existing_node_ids = {n["id"] for n in nodes}
    for req_id in matrix_df.index:
        if req_id not in existing_node_ids:
            continue
        for ep_id in matrix_df.columns:
            score = float(matrix_df.at[req_id, ep_id])
            if score >= threshold * 0.6:    # show edges above 60% of threshold
                edges.append({
                    "source": req_id,
                    "target": ep_id,
                    "score":  round(score, 4),
                    "color":  "#22C55E" if score >= threshold else "#F59E0B",
                    "width":  max(0.5, score * 3),
                })

    return nodes, edges


# ---------------------------------------------------------------------------
# Main pipeline orchestrator
# ---------------------------------------------------------------------------

async def run_analysis_pipeline(
    project_id: int,
    user_id: int,
    srs_path: str,
    v1_path: str,
    v2_path: Optional[str],
    threshold: float = 0.55,
    drift_threshold: float = -0.10,
    gt_path: Optional[str] = None,
) -> Dict:
    """
    Full pipeline: parse → embed → Qdrant → matrix → drift → graph.
    Returns a dict ready for AnalysisResult persistence.
    """
    logger.info("Starting analysis pipeline: project=%d, user=%d", project_id, user_id)

    embedder = get_embedder()
    vs       = get_vector_store()

    # Step 1: Parse
    requirements = extract_requirements(srs_path)
    endpoints_v1 = extract_endpoints(v1_path)
    endpoints_v2 = extract_endpoints(v2_path) if v2_path else None
    logger.info("Parsed %d requirements, %d endpoints_v1", len(requirements), len(endpoints_v1))

    # Step 2: Embed
    req_embeddings   = embedder.encode_requirements(requirements)
    ep_embeddings_v1 = embedder.encode_endpoints(endpoints_v1)

    # Step 3: Qdrant upsert
    if vs.is_available:
        vs.ensure_collection(dim=embedder.dim)
        vs.upsert_requirements(requirements, req_embeddings, user_id, project_id)
        vs.upsert_endpoints(endpoints_v1, ep_embeddings_v1, user_id, project_id)

    # Step 4: Build matrix (Qdrant search or NumPy fallback)
    if vs.is_available:
        matrix_df_v1 = vs.build_traceability_matrix(
            requirements, req_embeddings, endpoints_v1, user_id, project_id
        )
    else:
        sim = compute_similarity_matrix(req_embeddings, ep_embeddings_v1)
        matrix_df_v1 = build_traceability_matrix(requirements, endpoints_v1, sim, threshold)

    summary_v1 = get_compliance_summary(matrix_df_v1, requirements, threshold)
    overall    = compute_overall_compliance_score(matrix_df_v1, threshold=threshold)

    # Step 5: Drift detection
    drift_df = None
    drift_kpis = {}
    matrix_df_v2 = None
    ep_embeddings_v2 = None

    if endpoints_v2:
        ep_embeddings_v2 = embedder.encode_endpoints(endpoints_v2)
        if vs.is_available:
            vs.upsert_endpoints(endpoints_v2, ep_embeddings_v2, user_id, project_id)

        sim_v2       = compute_similarity_matrix(req_embeddings, ep_embeddings_v2)
        matrix_df_v2 = build_traceability_matrix(requirements, endpoints_v2, sim_v2, threshold)
        drift_df     = compute_drift_report(
            matrix_df_v1, matrix_df_v2,
            requirements, endpoints_v1, endpoints_v2,
            drift_threshold=drift_threshold,
        )
        drift_kpis = get_compliance_kpis(matrix_df_v1, matrix_df_v2, drift_df, threshold=threshold)

    # Step 6: Graph payload
    nodes, edges = build_graph_payload(
        requirements, endpoints_v1, endpoints_v2, matrix_df_v1, drift_df, threshold
    )

    # Step 7: Evaluation metrics (if ground truth available)
    # NB: the IR retrieval threshold is decoupled from the compliance threshold.
    # TF-IDF cosine lives on a much lower scale than transformer cosine, so both
    # methods are evaluated at a shared, calibrated tau (EVAL_THRESHOLD) for a
    # fair Precision/Recall/F1 comparison. MAP is threshold-independent.
    EVAL_THRESHOLD = 0.40
    metrics_data = {}
    if gt_path:
        try:
            gt = load_ground_truth(gt_path)
            report = generate_evaluation_report(requirements, endpoints_v1, matrix_df_v1, gt, threshold=EVAL_THRESHOLD)
            per_req = get_per_requirement_metrics(matrix_df_v1, gt, requirements, threshold=EVAL_THRESHOLD)
            tfidf_mat = compute_tfidf_similarity_matrix(requirements, endpoints_v1)
            t_map = compute_map(matrix_df_v1, gt, requirements)
            b_map = compute_map(tfidf_mat, gt, requirements)
            metrics_data = {
                "report": report.to_dict(),
                "per_requirement": per_req.to_dict(orient="records"),
                "eval_threshold": EVAL_THRESHOLD,
                "transformer_map": t_map,
                "tfidf_map": b_map,
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("Evaluation failed (non-fatal): %s", e)

    # Build serialisable responses
    cells = []
    for req_id in matrix_df_v1.index:
        for ep_id in matrix_df_v1.columns:
            score = float(matrix_df_v1.at[req_id, ep_id])
            cells.append({
                "req_id": req_id, "ep_id": ep_id,
                "score": round(score, 4),
                "category": get_compliance_category(score, threshold),
            })

    matrix_json = {
        "requirements": requirements,
        "endpoints":    endpoints_v1,
        "cells":        cells,
        "overall_compliance": round(overall, 4),
        "compliance_summary": summary_v1.to_dict(orient="records") if summary_v1 is not None else [],
    }

    drift_json = {}
    if drift_df is not None:
        drifted_pairs = get_drifted_pairs(drift_df)
        drift_json = {
            "pairs": drifted_pairs.to_dict(orient="records"),
            "compliance_v1": round(drift_kpis.get("compliance_v1", 0), 4),
            "compliance_v2": round(drift_kpis.get("compliance_v2", 0), 4),
            "n_drifted":     int(drift_kpis.get("n_drifted_pairs", 0)),
            "n_critical":    int(drift_kpis.get("n_critical", 0)),
            "n_major":       int(drift_kpis.get("n_major", 0)),
        }

    graph_json = {"nodes": nodes, "edges": edges}

    return {
        "matrix_json":  matrix_json,
        "drift_json":   drift_json,
        "metrics_json": metrics_data,
        "graph_json":   graph_json,
    }
