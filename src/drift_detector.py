"""
drift_detector.py — Multi-Version API Specification Drift Detector
====================================================================
Responsibility: Compare traceability matrices from two API versions and
                identify requirement–endpoint pairs that have experienced
                a meaningful semantic compliance regression (drift).

Algorithm:
  1. Align matrices by requirement ID.
  2. For shared endpoints (same path+method), compute delta = score_v2 - score_v1.
  3. For removed endpoints, score_v2 = 0.0.
  4. For added endpoints, score_v1 = 0.0.
  5. Classify severity by delta magnitude.
  6. Return a typed Drift Report DataFrame.
"""

import logging
import os
from typing import List, Dict, Optional, Tuple

import numpy as np
import pandas as pd
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# Default drift threshold from .env
DRIFT_THRESHOLD = float(os.getenv("DRIFT_THRESHOLD", "-0.10"))


# ---------------------------------------------------------------------------
# Severity classification
# ---------------------------------------------------------------------------

def classify_severity(delta: float) -> str:
    """
    Classify the severity of a score drop based on delta magnitude.

    Args:
        delta: score_v2 - score_v1. Negative means regression.

    Returns:
        One of: "No Drift", "Stable", "Minor Drift", "Major Drift", "Critical Drift"
    """
    if delta >= 0:
        return "No Drift"
    elif delta >= -0.10:
        return "Stable"        # Within noise margin; not a meaningful regression
    elif delta >= -0.25:
        return "Minor Drift"
    elif delta >= -0.50:
        return "Major Drift"
    else:
        return "Critical Drift"


def classify_severity_color(severity: str) -> str:
    """Map severity label to a hex colour for UI display."""
    return {
        "No Drift":      "#388E3C",   # Green
        "Stable":        "#4CAF50",   # Light green
        "Minor Drift":   "#FF9800",   # Orange
        "Major Drift":   "#F44336",   # Red
        "Critical Drift": "#B71C1C",  # Dark red
    }.get(severity, "#757575")


# ---------------------------------------------------------------------------
# Core drift computation
# ---------------------------------------------------------------------------

def compute_drift_report(
    matrix_v1: pd.DataFrame,
    matrix_v2: pd.DataFrame,
    requirements: List[Dict],
    endpoints_v1: List[Dict],
    endpoints_v2: List[Dict],
    drift_threshold: float = DRIFT_THRESHOLD,
) -> pd.DataFrame:
    """
    Compare two traceability matrices and produce a per-(requirement, endpoint)
    drift report.

    For each requirement and each endpoint that appears in v1 or v2:
      - score_v1: similarity score in v1 matrix (0.0 if endpoint not in v1)
      - score_v2: similarity score in v2 matrix (0.0 if endpoint not in v2)
      - delta:    score_v2 - score_v1
      - severity: classified by classify_severity()
      - drifted:  True if delta <= drift_threshold

    Args:
        matrix_v1:      Traceability matrix for API v1.0 (pd.DataFrame).
        matrix_v2:      Traceability matrix for API v2.0 (pd.DataFrame).
        requirements:   List of requirement dicts (for text lookup).
        endpoints_v1:   List of endpoint dicts from v1.
        endpoints_v2:   List of endpoint dicts from v2.
        drift_threshold: Delta threshold for flagging drift (default: -0.10).

    Returns:
        pd.DataFrame with columns:
          requirement_id | requirement_text | endpoint_id | score_v1 |
          score_v2 | delta | severity | drifted | endpoint_status
    """
    req_text_map = {r["id"]: r["text"] for r in requirements}
    req_title_map = {r["id"]: r["title"] for r in requirements}

    # Collect ALL endpoint IDs from both versions
    ep_ids_v1 = set(matrix_v1.columns.tolist())
    ep_ids_v2 = set(matrix_v2.columns.tolist())

    # Deduplicate by normalised path so a renamed endpoint (e.g. /api/v1/refund →
    # /api/v2/refund) produces ONE drift row, not one per version. Prefer the v2
    # id as the display id when present, else the v1 id.
    canonical_by_norm: Dict[str, str] = {}
    for ep_id in sorted(ep_ids_v1 | ep_ids_v2):
        norm = _normalise_endpoint_path(ep_id)
        if norm not in canonical_by_norm or ep_id in ep_ids_v2:
            canonical_by_norm[norm] = ep_id
    all_ep_ids = sorted(set(canonical_by_norm.values()))

    rows = []
    for req_id in matrix_v1.index:
        req_text = req_text_map.get(req_id, "")
        req_title = req_title_map.get(req_id, "")

        for ep_id in all_ep_ids:
            # Normalise endpoint ID for cross-version matching (strip version prefix)
            # e.g. "POST /api/v2/refund" → match to "POST /api/v1/refund"
            ep_id_v1 = _find_v1_equivalent(ep_id, ep_ids_v1)
            ep_id_v2 = _find_v2_equivalent(ep_id, ep_ids_v2)

            score_v1 = 0.0
            score_v2 = 0.0

            if ep_id_v1 and ep_id_v1 in matrix_v1.columns:
                score_v1 = float(matrix_v1.loc[req_id, ep_id_v1])
            if ep_id_v2 and ep_id_v2 in matrix_v2.columns:
                score_v2 = float(matrix_v2.loc[req_id, ep_id_v2])

            delta = score_v2 - score_v1
            severity = classify_severity(delta)
            drifted = delta <= drift_threshold

            # Endpoint status
            if ep_id_v1 and ep_id_v2:
                status = "Modified"
            elif ep_id_v1 and not ep_id_v2:
                status = "Removed"
            else:
                status = "Added"

            rows.append({
                "requirement_id": req_id,
                "requirement_title": req_title,
                "requirement_text": req_text,
                "endpoint_id": ep_id,
                "score_v1": round(score_v1, 4),
                "score_v2": round(score_v2, 4),
                "delta": round(delta, 4),
                "severity": severity,
                "drifted": drifted,
                "endpoint_status": status,
            })

    drift_df = pd.DataFrame(rows)
    n_drifted = int(drift_df["drifted"].sum())
    logger.info(
        "Drift report generated: %d total pairs, %d drifted (threshold=%.2f)",
        len(drift_df), n_drifted, drift_threshold
    )
    return drift_df


def _normalise_endpoint_path(ep_id: str) -> str:
    """
    Normalise endpoint ID by removing API version segment for cross-version matching.
    E.g.: "POST /api/v2/refund" → "POST /refund"
          "GET /api/v1/products/{product_id}" → "GET /products/{product_id}"
    """
    import re
    return re.sub(r"/api/v\d+(\.\d+)?", "", ep_id)


def _find_v1_equivalent(ep_id: str, ep_ids_v1: set) -> Optional[str]:
    """Find the v1 endpoint ID that matches the given endpoint (by normalised path)."""
    norm = _normalise_endpoint_path(ep_id)
    for v1_id in ep_ids_v1:
        if _normalise_endpoint_path(v1_id) == norm:
            return v1_id
    return None


def _find_v2_equivalent(ep_id: str, ep_ids_v2: set) -> Optional[str]:
    """Find the v2 endpoint ID that matches the given endpoint (by normalised path)."""
    norm = _normalise_endpoint_path(ep_id)
    for v2_id in ep_ids_v2:
        if _normalise_endpoint_path(v2_id) == norm:
            return v2_id
    return None


# ---------------------------------------------------------------------------
# Filtered views
# ---------------------------------------------------------------------------

def get_drifted_pairs(
    drift_df: pd.DataFrame,
    min_severity: str = "Minor Drift",
) -> pd.DataFrame:
    """
    Filter the drift report to only drifted requirement–endpoint pairs.

    Args:
        drift_df:     Full drift report DataFrame.
        min_severity: Minimum severity to include. Options:
                      "Minor Drift" | "Major Drift" | "Critical Drift"

    Returns:
        Filtered DataFrame, sorted by delta ascending (worst first).
    """
    severity_order = {
        "No Drift": 0, "Stable": 1,
        "Minor Drift": 2, "Major Drift": 3, "Critical Drift": 4
    }
    min_level = severity_order.get(min_severity, 2)
    filtered = drift_df[
        drift_df["severity"].map(severity_order).fillna(0) >= min_level
    ].copy()
    filtered = filtered.sort_values("delta", ascending=True)
    return filtered


def get_compliance_kpis(
    matrix_v1: pd.DataFrame,
    matrix_v2: pd.DataFrame,
    drift_df: pd.DataFrame,
    threshold: float = 0.75,
) -> Dict:
    """
    Compute KPI metrics for display on the Streamlit dashboard.

    Returns a dict with:
      - compliance_v1:      Overall compliance score for v1 (float, 0–1)
      - compliance_v2:      Overall compliance score for v2 (float, 0–1)
      - compliance_delta:   Difference (negative = regression)
      - n_drifted_pairs:    Number of (req, ep) pairs flagged as drifted
      - n_critical:         Number of critical drift pairs
      - n_missing_reqs:     Requirements with max_score_v2 < threshold (uncovered)
    """
    compliance_v1 = float((matrix_v1.max(axis=1) >= threshold).mean())
    compliance_v2 = float((matrix_v2.max(axis=1) >= threshold).mean())

    n_drifted = int(drift_df["drifted"].sum())
    n_critical = int((drift_df["severity"] == "Critical Drift").sum())
    n_missing = int((matrix_v2.max(axis=1) < threshold).sum())

    return {
        "compliance_v1": round(compliance_v1, 4),
        "compliance_v2": round(compliance_v2, 4),
        "compliance_delta": round(compliance_v2 - compliance_v1, 4),
        "n_drifted_pairs": n_drifted,
        "n_critical": n_critical,
        "n_missing_reqs": n_missing,
    }
