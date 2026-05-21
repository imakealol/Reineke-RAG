"""Qdrant access layer.

Responsibilities:

* Ensure the configured collection exists with the right vector size.
* Upsert chunk points carrying tenant/project/doc payload.
* Search with **mandatory** tenant + project filters (cross-tenant safety).
* Delete all points belonging to a document.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels
from qdrant_client.http.exceptions import UnexpectedResponse

from .config import settings
from .utils import deterministic_uuid, get_logger

log = get_logger(__name__)


class QdrantStoreError(RuntimeError):
    pass


@dataclass
class SearchHit:
    score: float
    payload: Dict[str, Any]
    point_id: str


class QdrantStore:
    def __init__(
        self,
        url: Optional[str] = None,
        api_key: Optional[str] = None,
        collection: Optional[str] = None,
    ) -> None:
        self.url = url or settings.QDRANT_URL
        self.api_key = api_key or settings.QDRANT_API_KEY
        self.collection = collection or settings.QDRANT_COLLECTION
        self.client = QdrantClient(url=self.url, api_key=self.api_key, prefer_grpc=False)

    # ---- lifecycle --------------------------------------------------------

    def ping(self) -> bool:
        try:
            self.client.get_collections()
            return True
        except Exception as exc:  # pragma: no cover
            log.warning("Qdrant ping failed: %s", exc)
            return False

    def ensure_collection(self, vector_size: int) -> None:
        """Create the collection if missing; verify the dimension if it exists."""
        try:
            existing = self.client.get_collection(self.collection)
        except (UnexpectedResponse, ValueError):
            existing = None
        except Exception as exc:
            # qdrant-client raises generic Exception on 404 in some versions
            if "not found" in str(exc).lower() or "404" in str(exc):
                existing = None
            else:
                raise QdrantStoreError(f"Failed to query collection: {exc}") from exc

        if existing is None:
            log.info(
                "Creating Qdrant collection '%s' (dim=%d, distance=Cosine)",
                self.collection,
                vector_size,
            )
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=qmodels.VectorParams(
                    size=vector_size, distance=qmodels.Distance.COSINE
                ),
            )
            self._ensure_payload_indexes()
            return

        # Verify dimension
        params = existing.config.params.vectors
        if isinstance(params, dict):
            # named vectors — not used here
            actual_size = next(iter(params.values())).size
        else:
            actual_size = params.size

        if actual_size != vector_size:
            raise QdrantStoreError(
                f"Qdrant collection '{self.collection}' was created with vector "
                f"dimension {actual_size}, but the embedding model "
                f"'{settings.EMBEDDING_MODEL}' produces vectors of dimension "
                f"{vector_size}. Either change EMBEDDING_MODEL, or recreate "
                f"the collection (see README troubleshooting)."
            )
        self._ensure_payload_indexes()

    def _ensure_payload_indexes(self) -> None:
        """Create indexes on filter fields. Idempotent — ignores 'already exists'."""
        for field, schema in (
            ("tenant", qmodels.PayloadSchemaType.KEYWORD),
            ("project", qmodels.PayloadSchemaType.KEYWORD),
            ("document_id", qmodels.PayloadSchemaType.KEYWORD),
            ("file_extension", qmodels.PayloadSchemaType.KEYWORD),
        ):
            try:
                self.client.create_payload_index(
                    collection_name=self.collection,
                    field_name=field,
                    field_schema=schema,
                )
            except Exception as exc:  # pragma: no cover
                # Index already exists — that's fine.
                log.debug("Payload index %s: %s", field, exc)

    # ---- writes -----------------------------------------------------------

    def upsert_chunks(
        self,
        *,
        document_id: str,
        vectors: List[List[float]],
        payloads: List[Dict[str, Any]],
    ) -> int:
        if not vectors:
            return 0
        if len(vectors) != len(payloads):
            raise QdrantStoreError("vectors / payloads length mismatch")

        points: List[qmodels.PointStruct] = []
        for i, (vec, payload) in enumerate(zip(vectors, payloads)):
            pid = deterministic_uuid(document_id, str(payload.get("chunk_index", i)))
            points.append(
                qmodels.PointStruct(id=pid, vector=vec, payload=payload)
            )

        self.client.upsert(collection_name=self.collection, points=points, wait=True)
        return len(points)

    def delete_document(self, document_id: str) -> int:
        flt = qmodels.Filter(
            must=[
                qmodels.FieldCondition(
                    key="document_id",
                    match=qmodels.MatchValue(value=document_id),
                )
            ]
        )

        try:
            count_before = self.client.count(
                collection_name=self.collection,
                count_filter=flt,
                exact=True,
            ).count
        except Exception:
            count_before = 0

        self.client.delete(
            collection_name=self.collection,
            points_selector=qmodels.FilterSelector(filter=flt),
            wait=True,
        )
        return count_before

    # ---- reads ------------------------------------------------------------

    def search(
        self,
        *,
        tenant: str,
        project: str,
        query_vector: List[float],
        top_k: int,
        score_threshold: Optional[float] = None,
    ) -> List[SearchHit]:
        if not tenant or not project:
            raise QdrantStoreError(
                "search() requires non-empty tenant and project — refusing "
                "unfiltered query (cross-tenant leak protection)."
            )

        flt = qmodels.Filter(
            must=[
                qmodels.FieldCondition(
                    key="tenant", match=qmodels.MatchValue(value=tenant)
                ),
                qmodels.FieldCondition(
                    key="project", match=qmodels.MatchValue(value=project)
                ),
            ]
        )

        results = self.client.search(
            collection_name=self.collection,
            query_vector=query_vector,
            query_filter=flt,
            limit=top_k,
            score_threshold=score_threshold,
            with_payload=True,
        )

        return [
            SearchHit(score=float(r.score), payload=dict(r.payload or {}), point_id=str(r.id))
            for r in results
        ]

    def get_points_by_ids(
        self,
        *,
        tenant: str,
        project: str,
        point_ids: List[str],
    ) -> List[SearchHit]:
        """Fetch known points by id with a tenant/project safety check.

        Used by the cross-turn citation recall path: chunks that were cited
        in a recent assistant message get pulled back into the candidate
        pool for the next query, so a follow-up like "tell me about the
        other one you cited" can still find the original document.

        Returns SearchHits with ``score=0.0`` — the reranker will compute
        the real relevance against the new query. Tenant/project mismatches
        are silently dropped (defensive: prevents a stale stored id from
        leaking content into the wrong collection).
        """
        if not point_ids:
            return []
        if not tenant or not project:
            raise QdrantStoreError(
                "get_points_by_ids() requires non-empty tenant and project."
            )

        records = self.client.retrieve(
            collection_name=self.collection,
            ids=point_ids,
            with_payload=True,
            with_vectors=False,
        )

        out: List[SearchHit] = []
        for r in records:
            payload = dict(r.payload or {})
            if payload.get("tenant") != tenant or payload.get("project") != project:
                continue
            out.append(SearchHit(score=0.0, payload=payload, point_id=str(r.id)))
        return out
