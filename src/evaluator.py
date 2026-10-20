"""
evaluator.py — IR Evaluation Metrics: Precision, Recall, F1, MAP
=================================================================
Responsibility: Evaluate the traceability pipeline against manually
                annotated ground truth labels and compare against
                a TF-IDF baseline.

Metrics computed:
  - Precision @ threshold
  - Recall @ threshold
  - F1-Score (harmonic mean of P & R)
  - MAP (Mean Average Precision) — rank-aware metric

Baseline comparison:
  - TF-IDF cosine similarity (sklearn TfidfVectorizer + cosine_similarity)
"""

import logging
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity as sk_cosine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Ground truth loader
# ---------------------------------------------------------------------------

def load_ground_truth(filepath: str) -> Dict[str, List[str]]:
    """
    Load binary relevance annotations from a CSV file.

    Expected CSV format:
        requirement_id,endpoint_id,is_relevant
        REQ-01,POST /api/v1/auth/login,1
        ...

    Args:
        filepath: Path to traceability_labels.csv

    Returns:
        Dict mapping requirement_id → list of relevant endpoint_ids.
        E.g.: {"REQ-04": ["POST /api/v1/refund"]}
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Ground truth file not found: {filepath}")

    df = pd.read_csv(filepath)
    required_cols = {"requirement_id", "endpoint_id", "is_relevant"}
    if not required_cols.issubset(df.columns):
        raise ValueError(
            f"Ground truth CSV must contain columns: {required_cols}. "
            f"Found: {set(df.columns)}"
        )

    ground_truth: Dict[str, List[str]] = {}
    for _, row in df.iterrows():
        if int(row["is_relevant"]) == 1:
            req_id = str(row["requirement_id"])
            ep_id = str(row["endpoint_id"])
            ground_truth.setdefault(req_id, []).append(ep_id)

    logger.info(
        "Loaded ground truth: %d requirements with relevant links from %s",
        len(ground_truth), filepath
    )
    return ground_truth


# ---------------------------------------------------------------------------
# Core metric computations
# ---------------------------------------------------------------------------

def compute_precision_recall_f1(
    similarity_matrix: pd.DataFrame,
    ground_truth: Dict[str, List[str]],
    requirements: List[Dict],
    endpoints: List[Dict],
    threshold: float = 0.70,
) -> Dict[str, float]:
    """
    Compute macro-averaged Precision, Recall, and F1-Score at a given threshold.

    For each requirement:
      - Retrieved set: endpoints with score >= threshold
      - Relevant set:  endpoints in ground truth

    Args:
        similarity_matrix: N×M DataFrame (req_ids as index, ep_ids as columns).
        ground_truth:      Dict from load_ground_truth().
        requirements:      List of requirement dicts.
        endpoints:         List of endpoint dicts.
        threshold:         Score cutoff for retrieval decision.

    Returns:
        Dict with keys: "precision", "recall", "f1", "n_requirements"
    """
    all_precisions, all_recalls, all_f1s = [], [], []

    for req in requirements:
        req_id = req["id"]
        if req_id not in ground_truth:
            logger.warning("No ground truth for %s — skipping.", req_id)
            continue

        relevant = set(ground_truth[req_id])

        # Retrieved: columns in the matrix with score >= threshold
        if req_id not in similarity_matrix.index:
            continue
        row = similarity_matrix.loc[req_id]
        retrieved = set(col for col in row.index if float(row[col]) >= threshold)

        tp = len(retrieved & relevant)
        precision = tp / len(retrieved) if retrieved else 0.0
        recall = tp / len(relevant) if relevant else 0.0
        f1 = (
            (2 * precision * recall) / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )

        all_precisions.append(precision)
        all_recalls.append(recall)
        all_f1s.append(f1)

        logger.debug(
            "%s → P=%.4f, R=%.4f, F1=%.4f | Retrieved=%d, Relevant=%d, TP=%d",
            req_id, precision, recall, f1, len(retrieved), len(relevant), tp
        )

    return {
        "precision": float(np.mean(all_precisions)) if all_precisions else 0.0,
        "recall": float(np.mean(all_recalls)) if all_recalls else 0.0,
        "f1": float(np.mean(all_f1s)) if all_f1s else 0.0,
        "n_requirements": len(all_precisions),
    }


def compute_map(
    similarity_matrix: pd.DataFrame,
    ground_truth: Dict[str, List[str]],
    requirements: List[Dict],
) -> float:
    """
    Compute Mean Average Precision (MAP) — a rank-aware evaluation metric.

    For each requirement:
      1. Rank all endpoints by similarity score (descending).
      2. Compute Average Precision (AP): sum of P@k for each relevant endpoint,
         divided by total number of relevant endpoints.
    MAP = mean of AP across all requirements.

    Args:
        similarity_matrix: N×M DataFrame.
        ground_truth:      Dict from load_ground_truth().
        requirements:      List of requirement dicts.

    Returns:
        MAP score (float in [0.0, 1.0]).
    """
    average_precisions = []

    for req in requirements:
        req_id = req["id"]
        if req_id not in ground_truth or req_id not in similarity_matrix.index:
            continue

        relevant = set(ground_truth[req_id])
        row = similarity_matrix.loc[req_id]

        # Rank endpoints by score descending
        ranked = row.sort_values(ascending=False)

        hits = 0
        precision_sum = 0.0
        for rank, (ep_id, _) in enumerate(ranked.items(), start=1):
            if ep_id in relevant:
                hits += 1
                precision_sum += hits / rank

        ap = precision_sum / len(relevant) if relevant else 0.0
        average_precisions.append(ap)
        logger.debug("%s → AP=%.4f", req_id, ap)

    map_score = float(np.mean(average_precisions)) if average_precisions else 0.0
    logger.info("MAP = %.4f (over %d requirements)", map_score, len(average_precisions))
    return map_score


# ---------------------------------------------------------------------------
# TF-IDF Baseline
# ---------------------------------------------------------------------------

def compute_tfidf_similarity_matrix(
    requirements: List[Dict],
    endpoints: List[Dict],
) -> pd.DataFrame:
    """
    Compute a TF-IDF cosine similarity matrix as a baseline comparison.

    Fits a TfidfVectorizer on the combined corpus of requirement + endpoint
    texts, then computes cosine similarity between all pairs.

    Returns:
        pd.DataFrame with same structure as the transformer matrix
        (req_ids as index, ep_ids as columns).
    """
    req_texts = [r["text"] for r in requirements]
    ep_texts = [e["text"] for e in endpoints]
    all_texts = req_texts + ep_texts

    vectorizer = TfidfVectorizer(
        ngram_range=(1, 2),
        max_df=0.95,
        min_df=1,
        sublinear_tf=True,
    )
    tfidf_matrix = vectorizer.fit_transform(all_texts)

    n_req = len(req_texts)
    req_vecs = tfidf_matrix[:n_req]
    ep_vecs = tfidf_matrix[n_req:]

    sim_matrix = sk_cosine(req_vecs, ep_vecs)
    sim_matrix = np.clip(sim_matrix, 0.0, 1.0)

    req_ids = [r["id"] for r in requirements]
    ep_ids = [e["id"] for e in endpoints]

    df = pd.DataFrame(sim_matrix, index=req_ids, columns=ep_ids)
    df.index.name = "requirement_id"
    df.columns.name = "endpoint_id"

    logger.info("Computed TF-IDF baseline matrix: %s", df.shape)
    return df


# ---------------------------------------------------------------------------
# Evaluation report generator
# ---------------------------------------------------------------------------

def generate_evaluation_report(
    requirements: List[Dict],
    endpoints: List[Dict],
    transformer_matrix: pd.DataFrame,
    ground_truth: Dict[str, List[str]],
    threshold: float = 0.70,
) -> pd.DataFrame:
    """
    Compute and compare Transformer vs TF-IDF metrics in a single report table.

    Args:
        requirements:       List of requirement dicts.
        endpoints:          List of endpoint dicts.
        transformer_matrix: Similarity matrix from SemanticEmbedder.
        ground_truth:       Ground truth annotations.
        threshold:          Retrieval threshold.

    Returns:
        pd.DataFrame comparison table with methods as rows and metrics as columns.
    """
    # Transformer metrics
    t_prf = compute_precision_recall_f1(
        transformer_matrix, ground_truth, requirements, endpoints, threshold
    )
    t_map = compute_map(transformer_matrix, ground_truth, requirements)

    # TF-IDF baseline
    tfidf_matrix = compute_tfidf_similarity_matrix(requirements, endpoints)
    b_prf = compute_precision_recall_f1(
        tfidf_matrix, ground_truth, requirements, endpoints, threshold
    )
    b_map = compute_map(tfidf_matrix, ground_truth, requirements)

    report = pd.DataFrame([
        {
            "Method": "Transformer (all-mpnet-base-v2)",
            "Precision": round(t_prf["precision"], 4),
            "Recall":    round(t_prf["recall"], 4),
            "F1-Score":  round(t_prf["f1"], 4),
            "MAP":       round(t_map, 4),
        },
        {
            "Method": "TF-IDF Baseline (bigrams)",
            "Precision": round(b_prf["precision"], 4),
            "Recall":    round(b_prf["recall"], 4),
            "F1-Score":  round(b_prf["f1"], 4),
            "MAP":       round(b_map, 4),
        },
    ]).set_index("Method")

    improvement_map = ((t_map - b_map) / b_map * 100) if b_map > 0 else 0.0
    improvement_f1  = ((t_prf["f1"] - b_prf["f1"]) / b_prf["f1"] * 100) if b_prf["f1"] > 0 else 0.0

    logger.info(
        "Evaluation complete. Transformer vs TF-IDF: MAP improvement=+%.1f%%, F1 improvement=+%.1f%%",
        improvement_map, improvement_f1
    )

    return report


def get_per_requirement_metrics(
    similarity_matrix: pd.DataFrame,
    ground_truth: Dict[str, List[str]],
    requirements: List[Dict],
    threshold: float = 0.70,
) -> pd.DataFrame:
    """
    Compute per-requirement Precision, Recall, F1, and AP scores.

    Returns a DataFrame with one row per requirement.
    """
    rows = []
    for req in requirements:
        req_id = req["id"]
        if req_id not in ground_truth or req_id not in similarity_matrix.index:
            continue

        relevant = set(ground_truth[req_id])
        row = similarity_matrix.loc[req_id]
        retrieved = set(col for col in row.index if float(row[col]) >= threshold)

        tp = len(retrieved & relevant)
        precision = tp / len(retrieved) if retrieved else 0.0
        recall = tp / len(relevant) if relevant else 0.0
        f1 = (
            (2 * precision * recall) / (precision + recall)
            if (precision + recall) > 0 else 0.0
        )

        # Average Precision
        ranked = row.sort_values(ascending=False)
        hits = 0
        precision_sum = 0.0
        for rank, (ep_id, _) in enumerate(ranked.items(), start=1):
            if ep_id in relevant:
                hits += 1
                precision_sum += hits / rank
        ap = precision_sum / len(relevant) if relevant else 0.0

        rows.append({
            "requirement_id": req_id,
            "requirement_title": req.get("title", ""),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "avg_precision": round(ap, 4),
            "n_relevant": len(relevant),
            "n_retrieved": len(retrieved),
            "n_true_positive": tp,
        })

    return pd.DataFrame(rows).set_index("requirement_id")
