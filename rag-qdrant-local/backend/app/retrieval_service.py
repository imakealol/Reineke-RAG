"""Retrieval = embed query → Qdrant search (tenant+project filter) →
optional cross-encoder rerank → top-K."""

from __future__ import annotations

import asyncio
import re
from typing import Callable, ContextManager, List, Optional

from sqlalchemy.orm import Session

from . import rerank_settings as rerank_settings_module
from .config import settings
from .database import session_scope
from .ollama_client import OllamaClient
from .qdrant_store import QdrantStore, SearchHit
from .rerank_settings import EffectiveRerankSettings
from .utils import get_logger

log = get_logger(__name__)

SessionFactory = Callable[[], ContextManager[Session]]


# ---------------------------------------------------------------------------
# Query-time synonym expansion
# ---------------------------------------------------------------------------
# Some DACH/IT terms split into near-synonyms where the corpus and the user
# language don't always overlap. The eval set surfaced exactly one such gap
# (q09: "externe Dienstleister" → expected file is PL.ISMS017_…_Lieferanten),
# and bge-m3 alone doesn't bridge the two. Rather than rewriting via the LLM
# (slow, adds latency budget back to the loop) or pulling in a dictionary
# package (extra dep, mostly unused), we maintain a tiny hand-curated table
# of pairs that the eval has shown to matter.
#
# Format: trigger token → form to append. Case-insensitive substring match
# on whole words only. Bidirectional pairs declared explicitly so the
# behaviour is obvious — adding a pair never silently changes another.
#
# Expansion appends the synonym in parentheses after the original question:
#   "...externe Dienstleister?"  ⇒  "...externe Dienstleister? (Lieferanten)"
# A single embedding then covers both terms, no extra Qdrant call needed.

# Each entry is (stem, addition). The stem matches at the start of any
# word (``\b<stem>``) so all declensions and German compounds are caught:
# ``dienstleister`` matches Dienstleister/Dienstleistern/Dienstleister-Audit,
# but not Dienstleistung. Keep stems strict; broaden only when the eval
# proves a gap.
_QUERY_SYNONYMS: List[tuple[str, str]] = [
    # ISMS017 lives under "Lieferanten"; users ask in "Dienstleister".
    ("dienstleister", "Lieferanten"),
    ("lieferant", "Dienstleister"),
]


def _expand_query_with_synonyms(question: str) -> str:
    """Return ``question`` with any matching synonym(s) appended in
    parentheses. Idempotent — re-running on an already-expanded question
    is a no-op because the synonym is now part of the string."""
    if not question:
        return question
    lower = question.lower()
    additions: List[str] = []
    seen_lower = set()
    for trigger, addition in _QUERY_SYNONYMS:
        # Prefix at a word boundary — matches German declensions/compounds
        # like Lieferantenliste while skipping Lieferung / Dienstleistung.
        if not re.search(rf"\b{re.escape(trigger)}", lower):
            continue
        addition_lower = addition.lower()
        # Skip if the addition is already present, or queued from a
        # previous trigger in this loop.
        if addition_lower in lower or addition_lower in seen_lower:
            continue
        additions.append(addition)
        seen_lower.add(addition_lower)
    if not additions:
        return question
    return f"{question} ({', '.join(additions)})"


RerankFn = Callable[..., List[float]]


class RetrievalService:
    def __init__(
        self,
        ollama: Optional[OllamaClient] = None,
        store: Optional[QdrantStore] = None,
        session_factory: Optional[SessionFactory] = None,
        rerank_fn: Optional[RerankFn] = None,
    ) -> None:
        self.ollama = ollama or OllamaClient()
        self.store = store or QdrantStore()
        # session_factory is only needed when callers don't pass explicit
        # ``rerank_override`` — production wires the SQLite scope, tests
        # inject a stub or pass settings directly.
        self.session_factory: SessionFactory = session_factory or session_scope
        # rerank_fn defaults to the real bge-reranker singleton; tests
        # inject a no-network stub so the import chain stays light.
        self._rerank_fn: Optional[RerankFn] = rerank_fn

    def _load_rerank_fn(self) -> RerankFn:
        if self._rerank_fn is not None:
            return self._rerank_fn
        # Local import: the reranker module pulls in FlagEmbedding (heavy
        # transitively brings torch). Deferring lets retrieval still work
        # when the dependency isn't installed *and* reranking is disabled.
        from . import reranker
        self._rerank_fn = reranker.rerank
        return self._rerank_fn

    def _resolve_rerank(
        self, *, tenant: str, project: str
    ) -> EffectiveRerankSettings:
        with self.session_factory() as db:
            return rerank_settings_module.resolve(db, tenant=tenant, project=project)

    async def retrieve(
        self,
        *,
        tenant: str,
        project: str,
        question: str,
        top_k: Optional[int] = None,
        min_score: Optional[float] = None,
        rerank_override: Optional[EffectiveRerankSettings] = None,
    ) -> List[SearchHit]:
        k = top_k or settings.RETRIEVAL_TOP_K
        threshold = min_score if min_score is not None else settings.MIN_RETRIEVAL_SCORE

        rerank_cfg = rerank_override or await asyncio.to_thread(
            self._resolve_rerank, tenant=tenant, project=project
        )

        # Overfetch only if rerank is going to use the extra candidates —
        # otherwise we pay for vectors we will throw away.
        qdrant_k = max(k, rerank_cfg.overfetch_k) if rerank_cfg.enabled else k

        expanded = _expand_query_with_synonyms(question)
        vec = await self.ollama.embed(expanded)

        hits = await asyncio.to_thread(
            self.store.search,
            tenant=tenant,
            project=project,
            query_vector=vec,
            top_k=qdrant_k,
            score_threshold=threshold,
        )

        if not rerank_cfg.enabled or len(hits) <= 1:
            # Reranking a single hit is a no-op; reranking zero is undefined.
            return hits[:k]

        passages = [str((h.payload or {}).get("text") or "") for h in hits]
        try:
            scores = await asyncio.to_thread(
                self._load_rerank_fn(),
                query=expanded,
                passages=passages,
                model_name=rerank_cfg.model,
            )
        except Exception as exc:
            # Don't fail the user's query because the reranker hiccupped —
            # log loud, fall back to bi-encoder order. The /health probe
            # will surface the underlying issue separately.
            log.warning(
                "Reranker failed (%s); falling back to bi-encoder order for "
                "tenant=%s project=%s",
                exc, tenant, project,
            )
            return hits[:k]

        # Replace the bi-encoder score with the reranker score so downstream
        # display and downstream gating stay consistent with the new order.
        rescored: List[SearchHit] = []
        for h, s in zip(hits, scores):
            rescored.append(SearchHit(score=float(s), payload=h.payload, point_id=h.point_id))
        rescored.sort(key=lambda h: h.score, reverse=True)
        return rescored[:k]
