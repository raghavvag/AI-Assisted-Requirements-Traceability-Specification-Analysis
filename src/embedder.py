"""
embedder.py — Transformer Semantic Embedding Engine
=====================================================
Responsibility: Encode lists of text strings into dense, L2-normalised
                semantic embedding vectors using sentence-transformers.

Model default: all-mpnet-base-v2 (768-dim) — best general-purpose quality.
Alternative:   all-MiniLM-L6-v2 (384-dim)  — 3× faster, slightly lower quality.

Design decisions:
  - normalize_embeddings=True: L2-normalisation means dot product == cosine
    similarity, avoiding a separate division step in the similarity module.
  - Model is loaded once at __init__ and cached. Subsequent calls reuse it.
  - Batch encoding is used for efficiency on CPU.
"""

import logging
import os
from typing import List

import numpy as np
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

load_dotenv()
logger = logging.getLogger(__name__)

# Default model from .env or hardcoded fallback
DEFAULT_MODEL = os.getenv("EMBEDDING_MODEL", "all-mpnet-base-v2")


class SemanticEmbedder:
    """
    Wraps a sentence-transformers model to produce normalised dense embeddings.

    Usage:
        embedder = SemanticEmbedder()
        req_vecs  = embedder.encode(["All refund transactions must include..."])
        ep_vecs   = embedder.encode(["POST /api/v1/refund | Process a refund..."])
    """

    def __init__(self, model_name: str = DEFAULT_MODEL):
        """
        Load the sentence-transformer model. Downloads on first run (~420MB for
        all-mpnet-base-v2); cached to ~/.cache/huggingface on subsequent runs.

        Args:
            model_name: HuggingFace model identifier or local path.
        """
        self.model_name = model_name
        logger.info("Loading embedding model: %s", model_name)
        self.model = SentenceTransformer(model_name)
        self.embedding_dim = self.model.get_sentence_embedding_dimension()
        logger.info(
            "Model loaded. Embedding dimension: %d", self.embedding_dim
        )

    def encode(
        self,
        texts: List[str],
        batch_size: int = 32,
        show_progress: bool = False,
    ) -> np.ndarray:
        """
        Encode a list of text strings into L2-normalised dense vectors.

        Args:
            texts:         List of input text strings.
            batch_size:    Number of strings to encode per forward pass.
            show_progress: Show a tqdm progress bar (useful for large batches).

        Returns:
            np.ndarray of shape (len(texts), embedding_dim), dtype float32.
            Vectors are L2-normalised: ||v|| == 1.0 for all rows.

        Raises:
            ValueError: If texts is empty.
        """
        if not texts:
            raise ValueError("Cannot encode an empty list of texts.")

        embeddings = self.model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=show_progress,
            normalize_embeddings=True,   # L2-norm → dot product == cosine sim
            convert_to_numpy=True,
        )
        logger.debug(
            "Encoded %d texts → shape %s", len(texts), embeddings.shape
        )
        return embeddings.astype(np.float32)

    def encode_single(self, text: str) -> np.ndarray:
        """
        Convenience method: encode a single string.

        Returns:
            np.ndarray of shape (embedding_dim,), dtype float32.
        """
        return self.encode([text])[0]

    def encode_requirements(self, requirements: List[dict]) -> np.ndarray:
        """
        Encode a list of requirement dicts (from parser.extract_requirements).

        Args:
            requirements: List of {"id": ..., "title": ..., "text": ...}

        Returns:
            np.ndarray of shape (N, embedding_dim).
        """
        texts = [r["text"] for r in requirements]
        logger.info("Encoding %d requirements...", len(texts))
        return self.encode(texts)

    def encode_endpoints(self, endpoints: List[dict]) -> np.ndarray:
        """
        Encode a list of endpoint dicts (from parser.extract_endpoints).

        Args:
            endpoints: List of {"id": ..., "method": ..., "path": ..., "text": ...}

        Returns:
            np.ndarray of shape (M, embedding_dim).
        """
        texts = [e["text"] for e in endpoints]
        logger.info("Encoding %d endpoints...", len(texts))
        return self.encode(texts)

    @property
    def dim(self) -> int:
        """Return the embedding dimensionality."""
        return self.embedding_dim

    def __repr__(self) -> str:
        return f"SemanticEmbedder(model='{self.model_name}', dim={self.embedding_dim})"
