"""Retrieval = embed query → Qdrant search (tenant+project filter) → score gate."""

from __future__ import annotations

import asyncio
from typing import List, Optional

from .config import settings
from .ollama_client import OllamaClient
from .qdrant_store import QdrantStore, SearchHit


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

        vec = await self.ollama.embed(question)

        hits = await asyncio.to_thread(
            self.store.search,
            tenant=tenant,
            project=project,
            query_vector=vec,
            top_k=k,
            score_threshold=threshold,
        )
        return hits
