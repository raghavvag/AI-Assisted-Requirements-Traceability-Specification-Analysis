"""
vector_store.py — Qdrant Vector Database Integration
=====================================================
Responsibility: Replace the in-memory NumPy cosine similarity with Qdrant —
                a persistent, multi-tenant vector database running in Docker.

Multi-Tenancy Design:
  Every point stored in Qdrant carries a payload with:
    { user_id, project_id, doc_type, item_id, text }

  ALL search and scroll queries include a strict Filter on (user_id, project_id)
  so vector queries NEVER cross-pollinate between tenants.

Qdrant Docker setup:
  docker compose up -d  (see docker-compose.yml)
  Collection URL: http://localhost:6338   (HTTP port set in docker-compose.yml)
  Collection: traceability_vectors

Usage:
  vs = VectorStore()
  vs.ensure_collection(dim=768)
  vs.upsert_requirements(requirements, embeddings, user_id=1, project_id=5)
  vs.upsert_endpoints(endpoints, embeddings, user_id=1, project_id=5)
  matrix = vs.build_traceability_matrix(requirements, req_embeddings, user_id=1, project_id=5)
"""

import hashlib
import logging
import os
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

QDRANT_URL        = os.getenv("QDRANT_URL", "http://localhost:6338")
COLLECTION_NAME   = os.getenv("QDRANT_COLLECTION", "traceability_vectors")
QDRANT_TIMEOUT    = int(os.getenv("QDRANT_TIMEOUT", "10"))


# ---------------------------------------------------------------------------
# Helper: stable point ID from string key
# ---------------------------------------------------------------------------

def _point_id(key: str) -> int:
    """
    Generate a stable uint64 point ID from an arbitrary string key.
    Uses the first 8 bytes of the SHA-256 digest, ensuring the same
    (user_id, project_id, item_id) always maps to the same Qdrant point ID.
    """
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big") % (2**63)


def _req_point_key(user_id: int, project_id: int, req_id: str) -> str:
    return f"req::{user_id}::{project_id}::{req_id}"


def _ep_point_key(user_id: int, project_id: int, ep_id: str) -> str:
    return f"ep::{user_id}::{project_id}::{ep_id}"


# ---------------------------------------------------------------------------
# VectorStore class
# ---------------------------------------------------------------------------

class VectorStore:
    """
    Wraps the Qdrant client with multi-tenant traceability operations.

    All methods accept user_id and project_id and encode them as mandatory
    payload filters to prevent cross-tenant data leakage.
    """

    def __init__(self, url: str = QDRANT_URL, collection: str = COLLECTION_NAME):
        self.url        = url
        self.collection = collection
        self._client    = None
        self._available = False
        self._connect()

    def _connect(self) -> None:
        """Attempt to connect to Qdrant. Sets self._available = True on success."""
        try:
            from qdrant_client import QdrantClient  # type: ignore
            self._client = QdrantClient(url=self.url, timeout=QDRANT_TIMEOUT)
            # Quick health check
            self._client.get_collections()
            self._available = True
            logger.info("Connected to Qdrant at %s", self.url)
        except Exception as e:
            self._available = False
            logger.warning(
                "Qdrant not available at %s (%s). "
                "Vector store operations will be skipped. "
                "Run: docker compose up -d",
                self.url, e
            )

    @property
    def is_available(self) -> bool:
        """True if Qdrant is reachable and operational."""
        return self._available

    def ensure_collection(self, dim: int) -> bool:
        """
        Idempotently create the Qdrant collection for the given embedding dimension.

        Args:
            dim: Embedding dimensionality (384 for MiniLM, 768 for mpnet).

        Returns:
            True if collection is ready, False if Qdrant is unavailable.
        """
        if not self._available:
            return False

        from qdrant_client.models import Distance, VectorParams  # type: ignore

        existing = [c.name for c in self._client.get_collections().collections]
        if self.collection not in existing:
            self._client.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )
            logger.info(
                "Created Qdrant collection '%s' (dim=%d, Distance=COSINE)",
                self.collection, dim
            )
        else:
            logger.debug("Collection '%s' already exists.", self.collection)
        return True

    # ── Upsertion ─────────────────────────────────────────────────────────────

    def upsert_requirements(
        self,
        requirements: List[Dict],
        embeddings: np.ndarray,
        user_id: int,
        project_id: int,
    ) -> int:
        """
        Upsert requirement vectors into Qdrant with full tenant payload.

        Args:
            requirements: List of requirement dicts (id, title, text).
            embeddings:   L2-normalised embeddings, shape (N, dim).
            user_id:      Owner's user ID (tenant isolation).
            project_id:   Project ID (tenant isolation).

        Returns:
            Number of points upserted, or 0 if Qdrant unavailable.
        """
        if not self._available:
            return 0
        return self._upsert_points(
            items=requirements,
            embeddings=embeddings,
            user_id=user_id,
            project_id=project_id,
            doc_type="requirement",
            id_field="id",
            text_field="text",
            point_key_fn=_req_point_key,
            extra_payload_fn=lambda r: {"title": r.get("title", "")},
        )

    def upsert_endpoints(
        self,
        endpoints: List[Dict],
        embeddings: np.ndarray,
        user_id: int,
        project_id: int,
    ) -> int:
        """
        Upsert endpoint vectors into Qdrant with full tenant payload.

        Args:
            endpoints:  List of endpoint dicts (id, method, path, text).
            embeddings: L2-normalised embeddings, shape (M, dim).
            user_id:    Owner's user ID.
            project_id: Project ID.

        Returns:
            Number of points upserted, or 0 if Qdrant unavailable.
        """
        if not self._available:
            return 0
        return self._upsert_points(
            items=endpoints,
            embeddings=embeddings,
            user_id=user_id,
            project_id=project_id,
            doc_type="endpoint",
            id_field="id",
            text_field="text",
            point_key_fn=_ep_point_key,
            extra_payload_fn=lambda e: {
                "method": e.get("method", ""),
                "path":   e.get("path",   ""),
                "summary": e.get("summary", ""),
            },
        )

    def _upsert_points(
        self,
        items: List[Dict],
        embeddings: np.ndarray,
        user_id: int,
        project_id: int,
        doc_type: str,
        id_field: str,
        text_field: str,
        point_key_fn,
        extra_payload_fn=None,
        batch_size: int = 64,
    ) -> int:
        """Generic batch upsertion with tenant payload."""
        from qdrant_client.models import PointStruct  # type: ignore

        points = []
        for i, item in enumerate(items):
            item_id = item[id_field]
            key     = point_key_fn(user_id, project_id, item_id)
            payload = {
                "user_id":    user_id,
                "project_id": project_id,
                "doc_type":   doc_type,
                "item_id":    item_id,
                "text":       item.get(text_field, ""),
            }
            if extra_payload_fn:
                payload.update(extra_payload_fn(item))

            points.append(PointStruct(
                id=_point_id(key),
                vector=embeddings[i].tolist(),
                payload=payload,
            ))

        # Batch upsert
        total = 0
        for start in range(0, len(points), batch_size):
            batch = points[start: start + batch_size]
            self._client.upsert(collection_name=self.collection, points=batch)
            total += len(batch)

        logger.info(
            "Upserted %d %s vectors for user_id=%d, project_id=%d",
            total, doc_type, user_id, project_id
        )
        return total

    # ── Query: build traceability matrix ─────────────────────────────────────

    def _vector_search(self, query_vector, query_filter, limit: int):
        """
        Nearest-neighbour search that works across qdrant-client versions.
        `.search()` was removed in qdrant-client >= 1.12 in favour of
        `.query_points()` (returns a QueryResponse whose `.points` is the hit list).
        """
        if hasattr(self._client, "query_points"):
            return self._client.query_points(
                collection_name=self.collection,
                query=query_vector,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            ).points
        return self._client.search(  # pragma: no cover - legacy client
            collection_name=self.collection,
            query_vector=query_vector,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )

    def build_traceability_matrix(
        self,
        requirements: List[Dict],
        req_embeddings: np.ndarray,
        user_id: int,
        project_id: int,
        endpoints: List[Dict],
        top_k: int = 50,
    ) -> pd.DataFrame:
        """
        Build the N×M traceability matrix by querying Qdrant for each requirement.

        For each requirement vector, search Qdrant for the top-K most similar
        endpoint vectors, filtered strictly by (user_id, project_id, doc_type=endpoint).

        Args:
            requirements:   List of requirement dicts.
            req_embeddings: L2-normalised requirement embeddings, shape (N, dim).
            user_id:        Tenant user ID (enforced in Qdrant filter).
            project_id:     Project ID (enforced in Qdrant filter).
            endpoints:      Full list of endpoint dicts (for complete matrix columns).
            top_k:          Number of nearest endpoints to retrieve per requirement.

        Returns:
            pd.DataFrame with requirement IDs as index, endpoint IDs as columns,
            cosine similarity scores as values.
        """
        if not self._available:
            logger.warning(
                "Qdrant unavailable — returning empty matrix. "
                "Start Qdrant with: docker compose up -d"
            )
            req_ids = [r["id"] for r in requirements]
            ep_ids  = [e["id"] for e in endpoints]
            return pd.DataFrame(0.0, index=req_ids, columns=ep_ids)

        from qdrant_client.models import FieldCondition, Filter, MatchValue  # type: ignore

        # Mandatory tenant filter: (user_id=X AND project_id=Y AND doc_type=endpoint)
        search_filter = Filter(
            must=[
                FieldCondition(key="user_id",    match=MatchValue(value=user_id)),
                FieldCondition(key="project_id", match=MatchValue(value=project_id)),
                FieldCondition(key="doc_type",   match=MatchValue(value="endpoint")),
            ]
        )

        req_ids = [r["id"] for r in requirements]
        ep_ids  = [e["id"] for e in endpoints]

        # Initialise with zeros (unmatched endpoints get 0.0)
        matrix = pd.DataFrame(0.0, index=req_ids, columns=ep_ids)
        matrix.index.name   = "requirement_id"
        matrix.columns.name = "endpoint_id"

        for i, req in enumerate(requirements):
            results = self._vector_search(req_embeddings[i].tolist(), search_filter, top_k)
            for hit in results:
                ep_item_id = (hit.payload or {}).get("item_id", "")
                if ep_item_id in matrix.columns:
                    # score is cosine similarity in [−1, 1]; clip to [0, 1]
                    matrix.at[req["id"], ep_item_id] = max(0.0, float(hit.score))

        logger.info(
            "Built traceability matrix via Qdrant: %d req × %d ep (user=%d, project=%d)",
            len(req_ids), len(ep_ids), user_id, project_id
        )
        return matrix

    # ── Scroll: retrieve all endpoints for a project ──────────────────────────

    def scroll_endpoints(
        self, user_id: int, project_id: int, limit: int = 200
    ) -> List[Dict]:
        """
        Scroll all endpoint payloads for a project from Qdrant.
        Used to verify vectors are persisted and retrieve stored metadata.

        Returns:
            List of payload dicts with keys: item_id, text, method, path.
        """
        if not self._available:
            return []

        from qdrant_client.models import FieldCondition, Filter, MatchValue  # type: ignore

        scroll_filter = Filter(
            must=[
                FieldCondition(key="user_id",    match=MatchValue(value=user_id)),
                FieldCondition(key="project_id", match=MatchValue(value=project_id)),
                FieldCondition(key="doc_type",   match=MatchValue(value="endpoint")),
            ]
        )
        results, _ = self._client.scroll(
            collection_name=self.collection,
            scroll_filter=scroll_filter,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        return [r.payload for r in results]

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def delete_project_vectors(self, user_id: int, project_id: int) -> bool:
        """
        Delete all vectors belonging to a (user_id, project_id) pair.
        Called when a project is deleted from the UI.

        Returns:
            True if deletion succeeded, False if Qdrant unavailable.
        """
        if not self._available:
            return False

        from qdrant_client.models import FieldCondition, Filter, MatchValue  # type: ignore

        delete_filter = Filter(
            must=[
                FieldCondition(key="user_id",    match=MatchValue(value=user_id)),
                FieldCondition(key="project_id", match=MatchValue(value=project_id)),
            ]
        )
        self._client.delete(
            collection_name=self.collection,
            points_selector=delete_filter,
        )
        logger.info(
            "Deleted vectors for user_id=%d, project_id=%d", user_id, project_id
        )
        return True

    def get_collection_stats(self) -> Dict:
        """Return collection vector count and status, for the dashboard info panel."""
        if not self._available:
            return {"status": "unavailable", "vectors_count": 0}
        try:
            info = self._client.get_collection(self.collection)
            return {
                "status": info.status.value if hasattr(info.status, 'value') else str(info.status),
                "vectors_count": info.vectors_count or 0,
                "indexed_vectors_count": info.indexed_vectors_count or 0,
            }
        except Exception:
            return {"status": "collection_not_found", "vectors_count": 0}
