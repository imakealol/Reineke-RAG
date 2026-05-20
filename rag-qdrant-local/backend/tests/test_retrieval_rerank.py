"""RetrievalService — bi-encoder + optional cross-encoder rerank path.

The reranker itself (FlagEmbedding wrapper) is not exercised here — we
inject a stub ``rerank_fn`` so the test stays offline and fast. What we
verify is the *integration*: overfetch logic, rerank-then-sort, error
fallback, and the no-op cases (rerank disabled, ≤1 hit).
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import List, Optional

import pytest

from app.qdrant_store import SearchHit
from app.rerank_settings import EffectiveRerankSettings
from app.retrieval_service import RetrievalService


class _StubOllama:
    async def embed(self, text: str) -> List[float]:
        return [0.0] * 4


class _StubStore:
    """Returns a fixed list of hits, ignoring the actual query vector."""

    def __init__(self, hits: List[SearchHit]) -> None:
        self._hits = hits
        self.last_top_k: Optional[int] = None
        self.last_filter: Optional[dict] = None

    def search(self, *, tenant, project, query_vector, top_k, score_threshold=None):
        self.last_top_k = top_k
        self.last_filter = {"tenant": tenant, "project": project}
        # Honor top_k slicing so we can verify the overfetch behavior.
        return list(self._hits[:top_k])


def _hit(file_name: str, score: float, idx: int = 0) -> SearchHit:
    return SearchHit(
        score=score,
        payload={
            "file_name": file_name,
            "document_id": f"doc-{file_name}",
            "chunk_index": idx,
            "text": f"body of {file_name}",
        },
        point_id=f"pt-{file_name}-{idx}",
    )


def _settings(
    *, enabled: bool, overfetch_k: int = 20, model: str = "stub/model",
) -> EffectiveRerankSettings:
    return EffectiveRerankSettings(
        enabled=enabled,
        overfetch_k=overfetch_k,
        model=model,
        enabled_source="override",
        overfetch_k_source="override",
        model_source="override",
        doc_count=999,
    )


@pytest.mark.asyncio
async def test_rerank_disabled_returns_top_k_from_qdrant_unchanged():
    hits = [_hit(f"f{i}.docx", score=1.0 - i * 0.01) for i in range(10)]
    store = _StubStore(hits)

    svc = RetrievalService(
        ollama=_StubOllama(),  # type: ignore[arg-type]
        store=store,           # type: ignore[arg-type]
        rerank_fn=lambda **_kw: pytest.fail("rerank_fn must not be called when disabled"),  # type: ignore[arg-type]
    )

    out = await svc.retrieve(
        tenant="t", project="p", question="hi",
        top_k=5,
        rerank_override=_settings(enabled=False),
    )

    assert [h.payload["file_name"] for h in out] == [f"f{i}.docx" for i in range(5)]
    # Qdrant was asked for top_k only — no overfetch when rerank is off.
    assert store.last_top_k == 5


@pytest.mark.asyncio
async def test_rerank_enabled_overfetches_then_reorders():
    """Reranker assigns scores that invert the bi-encoder order; the
    final result must reflect the reranker's order, sliced to top_k."""
    hits = [_hit(f"f{i}.docx", score=1.0 - i * 0.01) for i in range(10)]
    store = _StubStore(hits)

    # Stub reranker: scores are reversed (later docs win).
    def stub_rerank(*, query, passages, model_name=None):
        return [float(i) for i in range(len(passages))]

    svc = RetrievalService(
        ollama=_StubOllama(),  # type: ignore[arg-type]
        store=store,           # type: ignore[arg-type]
        rerank_fn=stub_rerank,
    )

    out = await svc.retrieve(
        tenant="t", project="p", question="hi",
        top_k=3,
        rerank_override=_settings(enabled=True, overfetch_k=10),
    )

    # Reranker pushed the last three to the top.
    assert [h.payload["file_name"] for h in out] == ["f9.docx", "f8.docx", "f7.docx"]
    # Scores in the returned hits are now the reranker's scores, not Qdrant's.
    assert [h.score for h in out] == [9.0, 8.0, 7.0]
    # Qdrant was asked for the overfetched K, not top_k.
    assert store.last_top_k == 10


@pytest.mark.asyncio
async def test_rerank_overfetch_floor_is_top_k():
    """Even if a per-collection K is somehow set below top_k, the
    overfetch must rise to at least top_k so we don't shrink the
    candidate pool."""
    hits = [_hit(f"f{i}.docx", score=1.0 - i * 0.01) for i in range(20)]
    store = _StubStore(hits)

    def stub_rerank(*, query, passages, model_name=None):
        return [-float(i) for i in range(len(passages))]

    svc = RetrievalService(
        ollama=_StubOllama(),  # type: ignore[arg-type]
        store=store,           # type: ignore[arg-type]
        rerank_fn=stub_rerank,
    )

    await svc.retrieve(
        tenant="t", project="p", question="hi",
        top_k=8,
        rerank_override=_settings(enabled=True, overfetch_k=3),  # nonsense
    )
    assert store.last_top_k == 8  # max(8, 3) = 8


@pytest.mark.asyncio
async def test_rerank_failure_falls_back_to_bi_encoder_order():
    hits = [_hit(f"f{i}.docx", score=1.0 - i * 0.01) for i in range(5)]
    store = _StubStore(hits)

    def broken_rerank(**_kw):
        raise RuntimeError("model OOM, simulated")

    svc = RetrievalService(
        ollama=_StubOllama(),  # type: ignore[arg-type]
        store=store,           # type: ignore[arg-type]
        rerank_fn=broken_rerank,
    )

    out = await svc.retrieve(
        tenant="t", project="p", question="hi",
        top_k=3,
        rerank_override=_settings(enabled=True, overfetch_k=5),
    )

    # Failure must not propagate — caller sees bi-encoder-ordered hits.
    assert [h.payload["file_name"] for h in out] == ["f0.docx", "f1.docx", "f2.docx"]


@pytest.mark.asyncio
async def test_rerank_skips_when_single_or_zero_hits():
    """One candidate has nothing to reorder, zero is empty — neither
    should pay for a reranker call."""
    store = _StubStore([_hit("only.docx", 0.7)])
    rerank_calls: list = []

    def counting_rerank(**kw):
        rerank_calls.append(kw)
        return [0.0]

    svc = RetrievalService(
        ollama=_StubOllama(),  # type: ignore[arg-type]
        store=store,           # type: ignore[arg-type]
        rerank_fn=counting_rerank,
    )

    out = await svc.retrieve(
        tenant="t", project="p", question="hi",
        top_k=5,
        rerank_override=_settings(enabled=True, overfetch_k=10),
    )
    assert len(out) == 1
    assert rerank_calls == []


@pytest.mark.asyncio
async def test_rerank_preserves_payload_and_point_id():
    """When the reranker reorders, every hit must keep its original
    payload and point_id — only ``score`` is replaced."""
    hits = [_hit(f"f{i}.docx", score=0.5) for i in range(3)]
    store = _StubStore(hits)

    svc = RetrievalService(
        ollama=_StubOllama(),  # type: ignore[arg-type]
        store=store,           # type: ignore[arg-type]
        rerank_fn=lambda **_kw: [0.1, 0.9, 0.5],
    )

    out = await svc.retrieve(
        tenant="t", project="p", question="hi",
        top_k=3,
        rerank_override=_settings(enabled=True, overfetch_k=3),
    )

    # Top of the new order is f1 (its 0.9 rerank score won).
    assert out[0].payload["file_name"] == "f1.docx"
    assert out[0].point_id == "pt-f1.docx-0"
    assert out[0].score == 0.9
