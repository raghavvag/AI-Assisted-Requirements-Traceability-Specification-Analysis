"""
src/alignment/scorer.py — AlignmentScorer implementations
========================================================
`EmbeddingAlignmentScorer` is the working default (bi-encoder + cosine, reusing
`src.embedder.SemanticEmbedder`). `SiameseBertAlignmentScorer` and
`CrossEncoderReranker` are roadmap Phase D stubs with a finalised interface.
"""

from __future__ import annotations

from typing import List, Optional

import numpy as np

from src.alignment.base import (
    AlignmentBand,
    AlignmentResult,
    ArtifactUnit,
    ContextUnit,
    GatePolicy,
)
from src.embedder import SemanticEmbedder
from src.similarity import compute_similarity_matrix


class EmbeddingAlignmentScorer:
    """
    Bi-encoder alignment: embed both sides with a sentence-transformer, score by
    cosine similarity, rank artefacts per context. This is exactly what the v1.0
    traceability pipeline does — now behind the AlignmentScorer interface.
    """

    method = "embedding-cosine"

    def __init__(self, embedder: Optional[SemanticEmbedder] = None,
                 policy: Optional[GatePolicy] = None):
        self.embedder = embedder or SemanticEmbedder()
        self.policy = policy or GatePolicy()

    def score(
        self,
        contexts: List[ContextUnit],
        artifacts: List[ArtifactUnit],
    ) -> List[AlignmentResult]:
        if not contexts or not artifacts:
            return []

        ctx_vecs = self.embedder.encode([c.embedding_text() for c in contexts])
        art_vecs = self.embedder.encode([a.embedding_text() for a in artifacts])
        matrix = compute_similarity_matrix(ctx_vecs, art_vecs)   # (N, M) in [0, 1]

        results: List[AlignmentResult] = []
        for i, ctx in enumerate(contexts):
            row = matrix[i]
            order = np.argsort(row)[::-1]
            rank_of = {int(j): r + 1 for r, j in enumerate(order)}
            for j, art in enumerate(artifacts):
                s = float(row[j])
                results.append(AlignmentResult(
                    context_id=ctx.id,
                    artifact_id=art.id,
                    score=round(s, 4),
                    method=self.method,
                    band=self.policy.band(s),
                    rank=rank_of[j],
                    evidence=_shared_terms(ctx.embedding_text(), art.embedding_text()),
                ))
        return results


def _shared_terms(a: str, b: str, k: int = 6) -> str:
    """Cheap lexical evidence hint — the salient words both texts share."""
    stop = {
        "the", "a", "an", "and", "or", "of", "to", "in", "for", "is", "are", "be",
        "must", "with", "that", "this", "field", "response", "returns", "request",
    }
    aw = {w.strip(".,:|()").lower() for w in a.split()}
    bw = {w.strip(".,:|()").lower() for w in b.split()}
    shared = sorted(w for w in (aw & bw) if len(w) > 3 and w not in stop)
    return ", ".join(shared[:k])


# ===========================================================================
# STUBS — roadmap Phase D
# ===========================================================================

class SiameseBertAlignmentScorer:
    """
    Code-aware alignment using a Siamese / bi-encoder model trained on
    (natural language, code) pairs.

    Roadmap Phase D. Candidate models (drop-in — only the encoder changes):
      - jinaai/jina-embeddings-v2-base-code
      - Salesforce/codet5p-110m-embedding
      - microsoft/codebert-base  (mean-pooled)
      - Alibaba-NLP/gte-Qwen2-1.5B-instruct  (larger, NL+code)

    Interface is identical to EmbeddingAlignmentScorer.score(); implementation
    swaps the encoder and uses ArtifactUnit.embedding_text() built by
    PythonASTArtifactSource (signature + docstring + call neighbourhood).
    """

    method = "siamese-bert-code"

    def __init__(self, model_name: str = "jinaai/jina-embeddings-v2-base-code"):
        self.model_name = model_name

    def score(self, contexts, artifacts):  # pragma: no cover - stub
        raise NotImplementedError(
            "SiameseBertAlignmentScorer is roadmap Phase D — see "
            "docs/architecture/semantic-alignment-roadmap.md"
        )


class CrossEncoderReranker:
    """
    Decorator scorer: takes a base AlignmentScorer, keeps its top-K artefacts per
    context, then re-scores just those pairs with a cross-encoder for precision.

    Roadmap Phase D. `score()` delegates, truncates to top_k by base score, then
    replaces those rows' scores with cross-encoder probabilities.
    """

    def __init__(self, base: "EmbeddingAlignmentScorer", model_name: str,
                 top_k: int = 5):
        self.base, self.model_name, self.top_k = base, model_name, top_k

    def score(self, contexts, artifacts):  # pragma: no cover - stub
        raise NotImplementedError(
            "CrossEncoderReranker is roadmap Phase D — see "
            "docs/architecture/semantic-alignment-roadmap.md"
        )
