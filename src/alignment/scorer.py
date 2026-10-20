"""
src/alignment/scorer.py — AlignmentScorer implementations
========================================================
`EmbeddingAlignmentScorer` is the default (bi-encoder + cosine, reusing
`src.embedder.SemanticEmbedder`). `SiameseBertAlignmentScorer` scores code units
with a code embedder, and `CompositeAlignmentScorer` routes each artefact to one
of the two by kind (M5). `CrossEncoderReranker` is still a roadmap stub.
"""

from __future__ import annotations

from dataclasses import replace
from typing import List, Optional

import numpy as np

from src.alignment.base import (
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
        self.embedder = embedder if embedder is not None else SemanticEmbedder()
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
# Code-aware scoring (M5)
# ===========================================================================

CODE_KINDS = frozenset({"function", "class", "diff_hunk"})


class SiameseBertAlignmentScorer(EmbeddingAlignmentScorer):
    """
    Code-aware alignment: a bi-encoder trained on (natural language, code) pairs,
    default jinaai/jina-embeddings-v2-base-code. Same contract and cosine/rank
    logic as EmbeddingAlignmentScorer; only the encoder changes. Artefact text is
    the semantic fingerprint built by PythonASTArtifactSource.

    Without the model (offline, tests) `load_code_embedder` falls back to the
    deterministic LexicalCodeEmbedder; `method` says which one scored.
    """

    def __init__(self, embedder=None, policy: Optional[GatePolicy] = None,
                 model_name: str = "jinaai/jina-embeddings-v2-base-code"):
        from src.alignment.code_embedder import load_code_embedder

        super().__init__(embedder=embedder or load_code_embedder(model_name), policy=policy)
        self.method = f"siamese-bert-code:{getattr(self.embedder, 'model_name', model_name)}"


class CompositeAlignmentScorer:
    """
    Routes each artefact to a scorer by `ArtifactUnit.kind`: code units
    (function, class, diff_hunk) to the code scorer, everything else (endpoints,
    prose) to the text scorer. Results keep each scorer's `method`; `rank` is
    recomputed per context across all artefacts so the contract holds.
    """

    method = "composite"

    def __init__(self, text_scorer=None, code_scorer=None, code_kinds=CODE_KINDS):
        self.text_scorer = text_scorer or EmbeddingAlignmentScorer()
        self.code_scorer = code_scorer or SiameseBertAlignmentScorer()
        self.code_kinds = frozenset(code_kinds)

    def score(self, contexts: List[ContextUnit], artifacts: List[ArtifactUnit]) -> List[AlignmentResult]:
        if not contexts or not artifacts:
            return []
        code = [a for a in artifacts if a.kind in self.code_kinds]
        text = [a for a in artifacts if a.kind not in self.code_kinds]
        rows: List[AlignmentResult] = []
        if text:
            rows += self.text_scorer.score(contexts, text)
        if code:
            rows += self.code_scorer.score(contexts, code)

        order = {a.id: j for j, a in enumerate(artifacts)}
        by_ctx: dict = {}
        for r in rows:
            by_ctx.setdefault(r.context_id, []).append(r)
        out: List[AlignmentResult] = []
        for ctx in contexts:
            ranked = sorted(by_ctx.get(ctx.id, []), key=lambda r: (-r.score, order[r.artifact_id]))
            out += [replace(r, rank=i + 1) for i, r in enumerate(ranked)]
        out.sort(key=lambda r: ([c.id for c in contexts].index(r.context_id), order[r.artifact_id]))
        return out


# ===========================================================================
# STUB — roadmap Phase D (optional precision stage)
# ===========================================================================


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
