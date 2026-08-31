"""
backend/app/services/vector_store.py
======================================
Qdrant integration with strict multi-tenant payload filtering.
Collection: requirements_traceability (768-dim, COSINE)
"""

import hashlib
import logging
from typing import Dict, List, Optional

import numpy as np
from app.core.config import settings

logger = logging.getLogger(__name__)

COLLECTION = settings.QDRANT_COLLECTION


# ---------------------------------------------------------------------------
# Stable point ID helper
# ---------------------------------------------------------------------------

def _point_id(key: str) -> int:
    digest = hashlib.sha256(key.encode()).digest()
    return int.from_bytes(digest[:8], "big") % (2 ** 63)


def _req_key(user_id: int, project_id: int, req_id: str) -> str:
    return f"req::{user_id}::{project_id}::{req_id}"


def _ep_key(user_id: int, project_id: int, ep_id: str) -> str:
    return f"ep::{user_id}::{project_id}::{ep_id}"


# ---------------------------------------------------------------------------
# VectorStore
# ---------------------------------------------------------------------------

class VectorStore:
    """
    Qdrant vector store with mandatory (user_id, project_id) payload filtering.
    Fails gracefully when Qdrant is unavailable.
    """

    def __init__(self):
        self._client = None
        self._available = False
        self._connect()

    def _connect(self):
        try:
            from qdrant_client import QdrantClient
            self._client = QdrantClient(url=settings.QDRANT_URL, timeout=10)
            self._client.get_collections()
            self._available = True
            logger.info("Qdrant connected at %s", settings.QDRANT_URL)
        except Exception as e:
            logger.warning("Qdrant unavailable: %s", e)

    @property
    def is_available(self) -> bool:
        return self._available

    def ensure_collection(self, dim: int = 768) -> bool:
        if not self._available:
            return False
        from qdrant_client.models import Distance, VectorParams
        existing = [c.name for c in self._client.get_collections().collections]
        if COLLECTION not in existing:
            self._client.create_collection(
                collection_name=COLLECTION,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )
            logger.info("Created Qdrant collection '%s' (dim=%d)", COLLECTION, dim)
        return True

    # ── Upsert ───────────────────────────────────────────────────────────────

    def upsert_requirements(
        self,
        requirements: List[Dict],
        embeddings: np.ndarray,
        user_id: int,
        project_id: int,
    ) -> int:
        return self._upsert(
            requirements, embeddings, user_id, project_id,
            doc_type="requirement", id_field="id",
            extra_fn=lambda r: {"title": r.get("title", "")},
            key_fn=_req_key,
        )

    def upsert_endpoints(
        self,
        endpoints: List[Dict],
        embeddings: np.ndarray,
        user_id: int,
        project_id: int,
    ) -> int:
        return self._upsert(
            endpoints, embeddings, user_id, project_id,
            doc_type="endpoint", id_field="id",
            extra_fn=lambda e: {"method": e.get("method",""), "path": e.get("path","")},
            key_fn=_ep_key,
        )

    def _upsert(self, items, embeddings, user_id, project_id,
                doc_type, id_field, extra_fn, key_fn, batch=64):
        if not self._available:
            return 0
        from qdrant_client.models import PointStruct
        points = []
        for i, item in enumerate(items):
            item_id = item[id_field]
            key     = key_fn(user_id, project_id, item_id)
            payload = {
                "user_id": user_id, "project_id": project_id,
                "type": doc_type, "identifier": item_id,
                "text": item.get("text", ""),
                **(extra_fn(item) if extra_fn else {}),
            }
            points.append(PointStruct(id=_point_id(key), vector=embeddings[i].tolist(), payload=payload))
        total = 0
        for start in range(0, len(points), batch):
            self._client.upsert(COLLECTION, points=points[start:start+batch])
            total += len(points[start:start+batch])
        logger.info("Upserted %d %s vectors (user=%d, project=%d)", total, doc_type, user_id, project_id)
        return total

    # ── Search ───────────────────────────────────────────────────────────────

    def _vector_search(self, query_vector, query_filter, limit: int):
        """
        Nearest-neighbour search that works across qdrant-client versions.
        `.search()` was removed in qdrant-client >= 1.12 in favour of
        `.query_points()`, which returns a QueryResponse with `.points`.
        """
        if hasattr(self._client, "query_points"):
            return self._client.query_points(
                collection_name=COLLECTION,
                query=query_vector,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            ).points
        return self._client.search(  # pragma: no cover - legacy client
            collection_name=COLLECTION,
            query_vector=query_vector,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )

    def build_traceability_matrix(
        self,
        requirements: List[Dict],
        req_embeddings: np.ndarray,
        endpoints: List[Dict],
        user_id: int,
        project_id: int,
        top_k: int = 50,
    ):
        """
        Build N×M cosine similarity matrix via Qdrant search.
        Returns a pandas DataFrame.
        """
        import pandas as pd
        req_ids = [r["id"] for r in requirements]
        ep_ids  = [e["id"] for e in endpoints]
        matrix  = pd.DataFrame(0.0, index=req_ids, columns=ep_ids)
        matrix.index.name   = "requirement_id"
        matrix.columns.name = "endpoint_id"

        if not self._available:
            return matrix

        from qdrant_client.models import FieldCondition, Filter, MatchValue
        flt = Filter(must=[
            FieldCondition(key="user_id",    match=MatchValue(value=user_id)),
            FieldCondition(key="project_id", match=MatchValue(value=project_id)),
            FieldCondition(key="type",       match=MatchValue(value="endpoint")),
        ])
        for i, req in enumerate(requirements):
            hits = self._vector_search(req_embeddings[i].tolist(), flt, top_k)
            for hit in hits:
                ep_id = (hit.payload or {}).get("identifier", "")
                if ep_id in matrix.columns:
                    matrix.at[req["id"], ep_id] = max(0.0, float(hit.score))
        return matrix

    # ── Cleanup ──────────────────────────────────────────────────────────────

    def delete_project_vectors(self, user_id: int, project_id: int) -> bool:
        if not self._available:
            return False
        from qdrant_client.models import FieldCondition, Filter, MatchValue
        flt = Filter(must=[
            FieldCondition(key="user_id",    match=MatchValue(value=user_id)),
            FieldCondition(key="project_id", match=MatchValue(value=project_id)),
        ])
        self._client.delete(COLLECTION, points_selector=flt)
        return True

    def get_stats(self) -> Dict:
        if not self._available:
            return {"status": "unavailable", "vectors_count": 0}
        try:
            info = self._client.get_collection(COLLECTION)
            return {
                "status": str(info.status),
                "vectors_count": info.vectors_count or 0,
                "indexed_vectors_count": info.indexed_vectors_count or 0,
            }
        except Exception:
            return {"status": "collection_not_found", "vectors_count": 0}


# Singleton
_store: Optional[VectorStore] = None


def get_vector_store() -> VectorStore:
    global _store
    if _store is None:
        _store = VectorStore()
    return _store
