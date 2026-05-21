"""Retrieval = embed query → Qdrant search (tenant+project filter) →
optional cross-encoder rerank → stem-dedup → top-K."""

from __future__ import annotations

import asyncio
import os
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
# Stem-based deduplication
# ---------------------------------------------------------------------------
# Customers commonly have both the source DOCX and an exported PDF of the
# same document. Without dedup, both win retrieval slots for the same
# content, starving genuinely-different documents out of the top-K. We
# collapse by filename stem so each *logical* document gets at most one
# slot at this stage; finer-grained chunk dedup inside a document (page 2
# vs page 6 of the same PDF) stays — that's signal, not noise.
#
# Stem-stripping rules mirror the chunker so the join is symmetric:
# strip extension, strip ISO date suffix (_20251231), strip version suffix
# (_v2 / -v10). Two passes for names carrying both.

_FILENAME_DATE_SUFFIX_RE = re.compile(r"_\d{8}$")
_FILENAME_VERSION_SUFFIX_RE = re.compile(r"[_\-]v\d+$")


def _document_stem(file_name: str) -> str:
    """Return the comparison key for stem-dedup.

    Case-insensitive so ``Foo.PDF`` and ``foo.pdf`` collapse. Extension and
    bookkeeping suffixes (``_20251231``, ``_v2``) are stripped. Two passes
    in case a name carries both. Empty input returns empty string.
    """
    if not file_name:
        return ""
    stem, _ = os.path.splitext(file_name)
    stem = _FILENAME_DATE_SUFFIX_RE.sub("", stem)
    stem = _FILENAME_VERSION_SUFFIX_RE.sub("", stem)
    return stem.lower()


def _dedup_by_stem(hits: List[SearchHit]) -> List[SearchHit]:
    """Keep the highest-scoring hit per filename stem, preserving order.

    Hits without a ``file_name`` payload (rare, defensive) bypass dedup so a
    payload accident never silently drops them. Stems that resolve to empty
    also bypass — better to keep the hit than to merge unrelated documents.
    """
    seen: set[str] = set()
    out: List[SearchHit] = []
    for h in hits:
        file_name = str((h.payload or {}).get("file_name") or "")
        stem = _document_stem(file_name)
        if not stem:
            out.append(h)
            continue
        if stem in seen:
            continue
        seen.add(stem)
        out.append(h)
    return out


def _merge_unique_by_point_id(
    primary: List[SearchHit], extras: List[SearchHit]
) -> List[SearchHit]:
    """Append ``extras`` after ``primary``, skipping duplicates by point_id.

    Used to fold past-citation candidates into the fresh retrieval set
    without inflating the candidate pool when the same chunk shows up in
    both. ``primary`` order is preserved so the reranker's input still
    starts with the highest-confidence fresh candidates.
    """
    seen = {h.point_id for h in primary}
    merged = list(primary)
    for h in extras:
        if h.point_id in seen:
            continue
        seen.add(h.point_id)
        merged.append(h)
    return merged


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
        past_citation_ids: Optional[List[str]] = None,
    ) -> List[SearchHit]:
        """Run the retrieval pipeline for a single query.

        ``past_citation_ids`` (optional) carries Qdrant point IDs that were
        cited in recent assistant messages of this conversation. They are
        fetched as extra candidates (score=0 placeholder) and folded into
        the same reranking pass so the cross-encoder decides whether
        anything in conversation history is *still* relevant to the new
        question. Solves "tell me about the other one you cited" style
        follow-ups without an LLM-rewrite step.
        """
        k = top_k or settings.RETRIEVAL_TOP_K
        threshold = min_score if min_score is not None else settings.MIN_RETRIEVAL_SCORE

        rerank_cfg = rerank_override or await asyncio.to_thread(
            self._resolve_rerank, tenant=tenant, project=project
        )

        # Overfetch covers two needs: rerank candidates AND stem-dedup
        # slack. We always pull at least 2*k so the dedup step downstream
        # can drop DOCX/PDF duplicates without starving the final top-K.
        qdrant_k = max(k * 2, rerank_cfg.overfetch_k) if rerank_cfg.enabled else k * 2

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

        # Fold in recently-cited chunks if any. Done before reranking so the
        # cross-encoder can compare apples to apples: every candidate gets
        # a fresh relevance score for the *new* question.
        if past_citation_ids:
            past_hits = await asyncio.to_thread(
                self.store.get_points_by_ids,
                tenant=tenant,
                project=project,
                point_ids=past_citation_ids,
            )
            hits = _merge_unique_by_point_id(hits, past_hits)

        if not rerank_cfg.enabled or len(hits) <= 1:
            # Reranking a single hit is a no-op; reranking zero is undefined.
            return _dedup_by_stem(hits)[:k]

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
            return _dedup_by_stem(hits)[:k]

        # Replace the bi-encoder score with the reranker score so downstream
        # display and downstream gating stay consistent with the new order.
        rescored: List[SearchHit] = []
        for h, s in zip(hits, scores):
            rescored.append(SearchHit(score=float(s), payload=h.payload, point_id=h.point_id))
        rescored.sort(key=lambda h: h.score, reverse=True)
        return _dedup_by_stem(rescored)[:k]
