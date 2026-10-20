"""
src/alignment/code_embedder.py — Embedders for source-code units (M5)
=====================================================================
Same duck-typed interface as src.embedder.SemanticEmbedder (`encode(texts)`,
`encode_requirements(reqs)`, L2-normalised rows), so the review engine and the
alignment scorers can use either.

CodeEmbedder         jinaai/jina-embeddings-v2-base-code (sentence-transformers,
                     trust_remote_code=True): a bi-encoder trained on
                     natural-language <-> code pairs. ~300 MB, downloaded once.
LexicalCodeEmbedder  deterministic fallback with no model and no network:
                     identifiers split into words (snake_case / camelCase),
                     light stemming, hashed into a fixed-size bag of words with
                     sublinear term frequency. Used offline, in tests, or when
                     TRACEABILITY_CODE_MODEL=lexical.

load_code_embedder() tries the model and falls back, logging why.
"""

from __future__ import annotations

import logging
import math
import os
import re
import zlib
from typing import Dict, List, Sequence

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_CODE_MODEL = "jinaai/jina-embeddings-v2-base-code"
LEXICAL = "lexical"

# PR-gate drops that count as a regression for code units, per embedder. A code
# fingerprint is long and a field removal changes only part of it, so drops are
# smaller than for endpoint prose. Measured on the demo service (2026-10-20):
#   jina:    benign edits (log line, extra model field, reworded docstring) |d| <= 0.013;
#            dropping the refund transaction_id: -0.036 .. -0.048 on REQ-04
#   lexical: benign |d| <= 0.064; the same regression -0.27
# link_top_k is wider than for endpoints: one requirement is implemented by several
# code units (handler, models, policy helper, verifier). REQ-09's step-up policy
# function is its #4 match, so top-3 scoping would never check it.
CODE_MODEL_THRESHOLDS = {"regression_strong": -0.025, "regression_weak": -0.03, "link_top_k": 5}
LEXICAL_THRESHOLDS = {"regression_strong": -0.10, "regression_weak": -0.15, "link_top_k": 5}


def calibrated_thresholds(embedder: object) -> Dict[str, float]:
    """Code-gate threshold defaults for this embedder (the code model's for unknown ones)."""
    return dict(getattr(embedder, "calibrated_thresholds", CODE_MODEL_THRESHOLDS))

_STOP = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "into", "is", "it", "its", "of", "on",
    "or", "that", "the", "their", "this", "to", "with", "must", "shall", "should", "will", "can", "all", "each",
    "every", "any", "via", "per", "def", "self", "cls", "return", "returns", "none", "str", "int", "float",
    "bool", "optional", "list", "dict", "function", "method", "class", "async", "signature", "docstring",
    "calls", "reads", "keys", "fields", "field", "model", "request", "response", "route", "py", "app",
}


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def code_tokens(text: str) -> List[str]:
    """'RefundRequest.transaction_id' -> ['refund', 'request', 'transaction', 'id', 'transaction_id']."""
    out: List[str] = []
    for raw in re.findall(r"[A-Za-z][A-Za-z0-9_]*", text):
        if "_" in raw.strip("_"):
            out.append(raw.lower().strip("_"))            # keep the compound: 'transaction_id' is a strong signal
        for part in re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", raw).replace("_", " ").split():
            word = part.lower()
            if len(word) > 1 and word not in _STOP:
                out.append(_stem(word))
    return out


class LexicalCodeEmbedder:
    """Deterministic hashed bag-of-words embedder (no model, no network)."""

    model_name = LEXICAL
    calibrated_thresholds = LEXICAL_THRESHOLDS

    def __init__(self, dim: int = 4096):
        self.dim = dim

    def _vector(self, text: str) -> np.ndarray:
        counts: Dict[int, float] = {}
        for tok in code_tokens(text):
            idx = zlib.crc32(tok.encode("utf-8")) % self.dim
            counts[idx] = counts.get(idx, 0.0) + 1.0
        v = np.zeros(self.dim, dtype=np.float32)
        for idx, c in counts.items():
            v[idx] = 1.0 + math.log(c)                     # sublinear tf
        norm = float(np.linalg.norm(v))
        return v / norm if norm else v

    def encode(self, texts: Sequence[str], **_: object) -> np.ndarray:
        if not texts:
            raise ValueError("Cannot encode an empty list of texts.")
        return np.stack([self._vector(t) for t in texts])

    def encode_requirements(self, requirements: Sequence[dict]) -> np.ndarray:
        return self.encode([f"{r.get('title', '')}. {r['text']}" for r in requirements])


class CodeEmbedder:
    """Code-aware bi-encoder (default jina-embeddings-v2-base-code)."""

    calibrated_thresholds = CODE_MODEL_THRESHOLDS

    def __init__(self, model_name: str = DEFAULT_CODE_MODEL, max_seq_length: int = 1024):
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        logger.info("Loading code embedding model: %s", model_name)
        self.model = SentenceTransformer(model_name, trust_remote_code=True)
        self.model.max_seq_length = max_seq_length

    def encode(self, texts: Sequence[str], batch_size: int = 16, **_: object) -> np.ndarray:
        if not texts:
            raise ValueError("Cannot encode an empty list of texts.")
        vecs = self.model.encode(list(texts), batch_size=batch_size, normalize_embeddings=True,
                                 convert_to_numpy=True, show_progress_bar=False)
        return vecs.astype(np.float32)

    def encode_requirements(self, requirements: Sequence[dict]) -> np.ndarray:
        return self.encode([f"{r.get('title', '')}. {r['text']}" for r in requirements])


_CACHE: Dict[str, object] = {}


def load_code_embedder(model_name: str = DEFAULT_CODE_MODEL, *, allow_fallback: bool = True):
    """The code model (cached per process), or the lexical fallback when it cannot load.

    TRACEABILITY_CODE_MODEL overrides `model_name` ("lexical" forces the fallback).
    """
    model_name = os.getenv("TRACEABILITY_CODE_MODEL", "").strip() or model_name
    if model_name == LEXICAL:
        return LexicalCodeEmbedder()
    if model_name in _CACHE:
        return _CACHE[model_name]
    try:
        embedder = CodeEmbedder(model_name)
    except Exception as exc:  # noqa: BLE001 — offline, missing remote code, incompatible transformers ...
        if not allow_fallback:
            raise
        logger.warning("Code model %s unavailable (%s: %s); using the lexical fallback",
                       model_name, type(exc).__name__, exc)
        embedder = LexicalCodeEmbedder()
    _CACHE[model_name] = embedder
    return embedder
