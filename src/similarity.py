"""
similarity.py — Cosine Similarity & Traceability Matrix Builder
================================================================
Responsibility: Compute the N×M cosine similarity matrix between requirement
                embeddings and endpoint embeddings, then assemble it into a
                labelled Pandas DataFrame with compliance categories.

Design:
  - Uses sklearn.metrics.pairwise.cosine_similarity (handles L2-normalised
    input correctly; equivalent to matrix multiplication for unit vectors).
  - Compliance thresholds are configurable via .env / function args.
  - Output DataFrame has requirement IDs as index, endpoint IDs as columns.
"""

import logging
import os
from typing import List, Dict

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sklearn.metrics.pairwise import cosine_similarity

load_dotenv()
logger = logging.getLogger(__name__)

# Default thresholds (overridable via .env)
THRESHOLD_GREEN = float(os.getenv("THRESHOLD_GREEN", "0.75"))
THRESHOLD_AMBER = float(os.getenv("THRESHOLD_AMBER", "0.50"))


# ---------------------------------------------------------------------------
# Core similarity computation
# ---------------------------------------------------------------------------

def compute_similarity_matrix(
    req_embeddings: np.ndarray,
    endpoint_embeddings: np.ndarray,
) -> np.ndarray:
    """
    Compute the N×M cosine similarity matrix.

    Args:
        req_embeddings:      shape (N, D) — L2-normalised requirement vectors.
        endpoint_embeddings: shape (M, D) — L2-normalised endpoint vectors.

    Returns:
        np.ndarray of shape (N, M), dtype float32.
        Values are in [0.0, 1.0] for normalised inputs.
    """
    if req_embeddings.ndim != 2 or endpoint_embeddings.ndim != 2:
        raise ValueError(
            "Both embedding arrays must be 2-dimensional: (N, D) and (M, D)."
        )
    if req_embeddings.shape[1] != endpoint_embeddings.shape[1]:
        raise ValueError(
            f"Embedding dimension mismatch: reqs={req_embeddings.shape[1]}, "
            f"endpoints={endpoint_embeddings.shape[1]}"
        )

    matrix = cosine_similarity(req_embeddings, endpoint_embeddings)
    # Clip to [0, 1] — normalised inputs can produce tiny negative values due
    # to floating-point precision; clipping prevents confusing downstream logic.
    matrix = np.clip(matrix, 0.0, 1.0)
    logger.debug(
        "Computed similarity matrix: shape=%s, min=%.4f, max=%.4f",
        matrix.shape, matrix.min(), matrix.max()
    )
    return matrix.astype(np.float32)


# ---------------------------------------------------------------------------
# Compliance categorisation
# ---------------------------------------------------------------------------

def get_compliance_category(
    score: float,
    threshold_green: float = THRESHOLD_GREEN,
    threshold_amber: float = THRESHOLD_AMBER,
) -> str:
    """
    Map a cosine similarity score to a compliance category string.

    Args:
        score:            Cosine similarity value in [0.0, 1.0].
        threshold_green:  Minimum score for 'Compliant' (default 0.75).
        threshold_amber:  Minimum score for 'Partial' (default 0.50).

    Returns:
        One of: "Compliant", "Partial", "Non-Compliant"
    """
    if score >= threshold_green:
        return "Compliant"
    elif score >= threshold_amber:
        return "Partial"
    else:
        return "Non-Compliant"


def get_compliance_color(category: str) -> str:
    """Map a compliance category to a hex colour for Plotly visualisation."""
    return {
        "Compliant": "#388E3C",
        "Partial": "#F9A825",
        "Non-Compliant": "#D32F2F",
    }.get(category, "#757575")


# ---------------------------------------------------------------------------
# Traceability Matrix assembly
# ---------------------------------------------------------------------------

def build_traceability_matrix(
    requirements: List[Dict],
    endpoints: List[Dict],
    similarity_matrix: np.ndarray,
    threshold_green: float = THRESHOLD_GREEN,
    threshold_amber: float = THRESHOLD_AMBER,
) -> pd.DataFrame:
    """
    Assemble the N×M traceability matrix as a Pandas DataFrame.

    Rows    = requirement IDs (REQ-01 … REQ-10)
    Columns = endpoint IDs   (POST /api/v1/refund … )
    Values  = cosine similarity scores (float, 4 decimal places)

    Args:
        requirements:      List of requirement dicts (from parser).
        endpoints:         List of endpoint dicts (from parser).
        similarity_matrix: np.ndarray of shape (N, M).
        threshold_green:   Score threshold for 'Compliant'.
        threshold_amber:   Score threshold for 'Partial'.

    Returns:
        pd.DataFrame with:
          - index: requirement IDs
          - columns: endpoint IDs
          - values: cosine similarity scores (float32)
    """
    req_ids = [r["id"] for r in requirements]
    ep_ids = [e["id"] for e in endpoints]

    if similarity_matrix.shape != (len(req_ids), len(ep_ids)):
        raise ValueError(
            f"Matrix shape {similarity_matrix.shape} does not match "
            f"requirements ({len(req_ids)}) × endpoints ({len(ep_ids)})."
        )

    df = pd.DataFrame(
        similarity_matrix,
        index=req_ids,
        columns=ep_ids,
    )
    df.index.name = "requirement_id"
    df.columns.name = "endpoint_id"

    logger.info(
        "Built traceability matrix: %d requirements × %d endpoints",
        len(req_ids), len(ep_ids)
    )
    return df


def compute_overall_compliance_score(
    matrix_df: pd.DataFrame,
    threshold: float = THRESHOLD_GREEN,
) -> float:
    """
    Compute the overall compliance score as the fraction of requirements
    that have at least one endpoint scoring >= threshold.

    Args:
        matrix_df: Traceability matrix DataFrame (from build_traceability_matrix).
        threshold: Minimum score to count a requirement as satisfied.

    Returns:
        Float in [0.0, 1.0]. E.g., 0.90 means 90% of requirements are covered.
    """
    max_scores = matrix_df.max(axis=1)
    score = float((max_scores >= threshold).mean())
    logger.info("Overall compliance score: %.4f (threshold=%.2f)", score, threshold)
    return score


def get_compliance_summary(
    matrix_df: pd.DataFrame,
    requirements: List[Dict],
    threshold_green: float = THRESHOLD_GREEN,
    threshold_amber: float = THRESHOLD_AMBER,
) -> pd.DataFrame:
    """
    Produce a per-requirement compliance summary table.

    Returns a DataFrame with columns:
      requirement_id | requirement_title | max_score | best_endpoint |
      compliance_category | top_3_endpoints

    Args:
        matrix_df:    Traceability matrix.
        requirements: List of requirement dicts (for title lookup).
        threshold_green: Green threshold.
        threshold_amber: Amber threshold.
    """
    req_title_map = {r["id"]: r["title"] for r in requirements}
    rows = []

    for req_id in matrix_df.index:
        row_scores = matrix_df.loc[req_id]
        max_score = float(row_scores.max())
        best_ep = str(row_scores.idxmax())
        category = get_compliance_category(max_score, threshold_green, threshold_amber)

        # Top-3 endpoints by score
        top3 = row_scores.nlargest(3)
        top3_str = "; ".join(
            f"{ep} ({sc:.4f})" for ep, sc in top3.items()
        )

        rows.append({
            "requirement_id": req_id,
            "requirement_title": req_title_map.get(req_id, ""),
            "max_score": round(max_score, 4),
            "best_endpoint": best_ep,
            "compliance_category": category,
            "top_3_endpoints": top3_str,
        })

    summary_df = pd.DataFrame(rows).set_index("requirement_id")
    logger.info("Generated compliance summary for %d requirements.", len(rows))
    return summary_df


def get_top_endpoints_for_requirement(
    req_id: str,
    matrix_df: pd.DataFrame,
    top_k: int = 5,
) -> pd.Series:
    """
    Return the top-K scoring endpoints for a specific requirement.

    Args:
        req_id:    Requirement identifier (e.g., "REQ-04").
        matrix_df: Traceability matrix DataFrame.
        top_k:     Number of top endpoints to return.

    Returns:
        pd.Series with endpoint IDs as index and scores as values,
        sorted descending.
    """
    if req_id not in matrix_df.index:
        raise KeyError(f"Requirement '{req_id}' not found in matrix.")
    return matrix_df.loc[req_id].nlargest(top_k)
