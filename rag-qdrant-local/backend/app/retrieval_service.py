"""Retrieval = embed query → Qdrant search (tenant+project filter) → score gate."""

from __future__ import annotations

import asyncio
import re
from typing import List, Optional

from .config import settings
from .ollama_client import OllamaClient
from .qdrant_store import QdrantStore, SearchHit


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


class RetrievalService:
    def __init__(
        self,
        ollama: Optional[OllamaClient] = None,
        store: Optional[QdrantStore] = None,
    ) -> None:
        self.ollama = ollama or OllamaClient()
        self.store = store or QdrantStore()

    async def retrieve(
        self,
        *,
        tenant: str,
        project: str,
        question: str,
        top_k: Optional[int] = None,
        min_score: Optional[float] = None,
    ) -> List[SearchHit]:
        k = top_k or settings.RETRIEVAL_TOP_K
        threshold = min_score if min_score is not None else settings.MIN_RETRIEVAL_SCORE

        expanded = _expand_query_with_synonyms(question)
        vec = await self.ollama.embed(expanded)

        hits = await asyncio.to_thread(
            self.store.search,
            tenant=tenant,
            project=project,
            query_vector=vec,
            top_k=k,
            score_threshold=threshold,
        )
        return hits
