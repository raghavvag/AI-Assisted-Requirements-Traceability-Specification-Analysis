"""
scripts/snapshot_baseline_metrics.py — Record the published IR metrics
======================================================================
Computes Precision / Recall / F1 / MAP for the transformer model and the
TF-IDF baseline on the built-in e-commerce dataset (store_v1), exactly as
backend/app/services/pipeline.py does (retrieval threshold 0.40), and writes
tests/fixtures/baseline_metrics.json. Regression tests compare against it.

Run:  python scripts/snapshot_baseline_metrics.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.embedder import SemanticEmbedder  # noqa: E402
from src.evaluator import (  # noqa: E402
    compute_map,
    compute_precision_recall_f1,
    compute_tfidf_similarity_matrix,
    load_ground_truth,
)
from src.parser import extract_endpoints, extract_requirements  # noqa: E402
from src.similarity import build_traceability_matrix, compute_similarity_matrix  # noqa: E402

EVAL_THRESHOLD = 0.40          # same as pipeline.py
COMPLIANCE_THRESHOLD = 0.55
SRS = ROOT / "data" / "requirements" / "ecommerce_srs.md"
SPEC = ROOT / "data" / "api_specs" / "store_v1.json"
GT = ROOT / "data" / "ground_truth" / "traceability_labels.csv"
OUT = ROOT / "tests" / "fixtures" / "baseline_metrics.json"


def compute() -> dict:
    reqs = extract_requirements(str(SRS))
    eps = extract_endpoints(str(SPEC))
    gt = load_ground_truth(str(GT))
    emb = SemanticEmbedder()
    sim = compute_similarity_matrix(emb.encode_requirements(reqs), emb.encode_endpoints(eps))
    transformer = build_traceability_matrix(reqs, eps, sim, COMPLIANCE_THRESHOLD)
    tfidf = compute_tfidf_similarity_matrix(reqs, eps)

    def block(matrix) -> dict:
        prf = compute_precision_recall_f1(matrix, gt, reqs, eps, threshold=EVAL_THRESHOLD)
        out = {k.lower().replace("-", "_").replace(" ", "_"): round(float(v), 4) for k, v in prf.items()}
        out["map"] = round(float(compute_map(matrix, gt, reqs)), 4)
        return out

    return {
        "dataset": {"srs": "data/requirements/ecommerce_srs.md", "spec": "data/api_specs/store_v1.json",
                    "ground_truth": "data/ground_truth/traceability_labels.csv",
                    "n_requirements": len(reqs), "n_endpoints": len(eps)},
        "model": emb.model_name,
        "eval_threshold": EVAL_THRESHOLD,
        "transformer": block(transformer),
        "tfidf": block(tfidf),
        "recorded": date.today().isoformat(),
        "tolerance": 0.005,
    }


def main() -> None:
    data = compute()
    OUT.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(data, indent=2))
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
