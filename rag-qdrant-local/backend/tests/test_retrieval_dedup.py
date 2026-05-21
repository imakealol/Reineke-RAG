"""RetrievalService — stem-based dedup + cross-turn citation pool merge.

Both behaviours plug into ``retrieve()`` after the reranker. The unit tests
here cover the helpers in isolation; the integration test confirms that a
mixed DOCX/PDF candidate set actually collapses to one slot per logical
document by the time the caller sees the result.
"""

from __future__ import annotations

from typing import List, Optional

import pytest

from app.qdrant_store import SearchHit
from app.rerank_settings import EffectiveRerankSettings
from app.retrieval_service import (
    RetrievalService,
    _dedup_by_stem,
    _document_stem,
    _merge_unique_by_point_id,
)


# ---------------------------------------------------------------------------
# Helpers (mirror those in test_retrieval_rerank.py)
# ---------------------------------------------------------------------------

class _StubOllama:
    async def embed(self, text: str) -> List[float]:
        return [0.0] * 4


class _StubStore:
    def __init__(self, hits: List[SearchHit]) -> None:
        self._hits = hits
        self.last_top_k: Optional[int] = None
        self.point_id_calls: List[List[str]] = []
        self.extra_records: List[SearchHit] = []

    def search(self, *, tenant, project, query_vector, top_k, score_threshold=None):
        self.last_top_k = top_k
        return list(self._hits[:top_k])

    def get_points_by_ids(self, *, tenant, project, point_ids):
        self.point_id_calls.append(list(point_ids))
        return list(self.extra_records)


def _hit(file_name: str, score: float, idx: int = 0, doc_id: Optional[str] = None) -> SearchHit:
    return SearchHit(
        score=score,
        payload={
            "file_name": file_name,
            "document_id": doc_id or f"doc-{file_name}",
            "chunk_index": idx,
            "text": f"body of {file_name}",
        },
        point_id=f"pt-{file_name}-{idx}",
    )


def _settings(enabled: bool, overfetch_k: int = 20) -> EffectiveRerankSettings:
    return EffectiveRerankSettings(
        enabled=enabled,
        overfetch_k=overfetch_k,
        model="stub/model",
        enabled_source="override",
        overfetch_k_source="override",
        model_source="override",
        doc_count=999,
    )


# ---------------------------------------------------------------------------
# _document_stem — extension / version / date suffix stripping
# ---------------------------------------------------------------------------

def test_document_stem_strips_extension_and_lowercases():
    assert _document_stem("PL.ISMS006_Arbeit.pdf") == "pl.isms006_arbeit"
    assert _document_stem("Foo.DOCX") == "foo"


def test_document_stem_strips_iso_date_and_version_suffix():
    # _YYYYMMDD suffix → dropped.
    assert _document_stem("contract_20251231.pdf") == "contract"
    # _vN and -vN suffix → dropped.
    assert _document_stem("plan_v3.pdf") == "plan"
    assert _document_stem("plan-v12.docx") == "plan"
    # Combined: both suffixes peeled (date first, then version).
    assert _document_stem("plan_v2_20240101.pdf") == "plan"


def test_document_stem_empty_input_returns_empty():
    assert _document_stem("") == ""
    assert _document_stem(None) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _dedup_by_stem
# ---------------------------------------------------------------------------

def test_dedup_collapses_docx_and_pdf_pair_keeping_first():
    hits = [
        _hit("PL.ISMS006_Arbeit.pdf", 0.9),
        _hit("PL.ISMS006_Arbeit.docx", 0.8),
        _hit("PL.ISMS010_Backup.pdf", 0.7),
    ]
    out = _dedup_by_stem(hits)
    names = [h.payload["file_name"] for h in out]
    assert names == ["PL.ISMS006_Arbeit.pdf", "PL.ISMS010_Backup.pdf"]


def test_dedup_preserves_input_order_for_unique_stems():
    hits = [_hit(f"f{i}.pdf", 1.0 - i * 0.01) for i in range(5)]
    out = _dedup_by_stem(hits)
    assert [h.payload["file_name"] for h in out] == [f"f{i}.pdf" for i in range(5)]


def test_dedup_skips_hits_without_filename_payload():
    odd = SearchHit(score=0.5, payload={"document_id": "x"}, point_id="pt-x")
    hits = [odd, _hit("foo.pdf", 0.9)]
    out = _dedup_by_stem(hits)
    # Odd hit survives; foo.pdf survives too.
    assert len(out) == 2


# ---------------------------------------------------------------------------
# _merge_unique_by_point_id
# ---------------------------------------------------------------------------

def test_merge_unique_appends_only_new_point_ids():
    primary = [_hit("a.pdf", 0.9), _hit("b.pdf", 0.8)]
    extras = [
        _hit("a.pdf", 0.0),  # duplicate point_id → drop
        _hit("c.pdf", 0.0),
    ]
    merged = _merge_unique_by_point_id(primary, extras)
    assert [h.payload["file_name"] for h in merged] == ["a.pdf", "b.pdf", "c.pdf"]
    # Primary order intact.
    assert merged[0].score == 0.9 and merged[1].score == 0.8


def test_merge_unique_with_empty_extras_returns_primary_copy():
    primary = [_hit("a.pdf", 0.9)]
    merged = _merge_unique_by_point_id(primary, [])
    assert merged == primary
    # Must be a new list, not the same object (defensive against mutation).
    assert merged is not primary


# ---------------------------------------------------------------------------
# Integration — dedup actually fires inside retrieve()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retrieve_collapses_docx_pdf_pairs_in_top_k():
    """A candidate pool with one DOCX/PDF pair must surface as top_k=2
    distinct logical documents, not 'pair + 1 other'."""
    hits = [
        _hit("PL.ISMS006_Arbeit.pdf", 0.95),
        _hit("PL.ISMS006_Arbeit.docx", 0.94),
        _hit("PL.ISMS010_Backup.pdf", 0.93),
        _hit("PL.ISMS007_Kennwort.pdf", 0.92),
    ]
    store = _StubStore(hits)
    svc = RetrievalService(
        ollama=_StubOllama(),       # type: ignore[arg-type]
        store=store,                # type: ignore[arg-type]
        rerank_fn=lambda **_kw: pytest.fail("rerank disabled — shouldn't run"),  # type: ignore[arg-type]
    )

    out = await svc.retrieve(
        tenant="t", project="p", question="hi",
        top_k=2,
        rerank_override=_settings(enabled=False),
    )

    names = [h.payload["file_name"] for h in out]
    assert names == ["PL.ISMS006_Arbeit.pdf", "PL.ISMS010_Backup.pdf"]


@pytest.mark.asyncio
async def test_retrieve_past_citation_ids_join_candidate_pool():
    """When past_citation_ids are supplied, the store is asked for them
    and the reranker sees the combined pool."""
    fresh_hits = [_hit("fresh.pdf", 0.9)]
    store = _StubStore(fresh_hits)
    store.extra_records = [_hit("cited_earlier.pdf", 0.0, doc_id="doc-prev")]

    seen_passages: List[List[str]] = []

    def stub_rerank(*, query, passages, model_name=None):
        seen_passages.append(list(passages))
        # Score the previously-cited chunk higher so it surfaces.
        return [0.1, 0.9]

    svc = RetrievalService(
        ollama=_StubOllama(),       # type: ignore[arg-type]
        store=store,                # type: ignore[arg-type]
        rerank_fn=stub_rerank,
    )

    out = await svc.retrieve(
        tenant="t", project="p", question="and what about the other one",
        top_k=2,
        rerank_override=_settings(enabled=True, overfetch_k=4),
        past_citation_ids=["pt-cited_earlier.pdf-0"],
    )

    # Store was asked to materialise the past citation.
    assert store.point_id_calls == [["pt-cited_earlier.pdf-0"]]
    # Reranker saw two passages — fresh + recalled.
    assert len(seen_passages[0]) == 2
    # Recalled chunk won the rerank and appears in the output.
    names = [h.payload["file_name"] for h in out]
    assert "cited_earlier.pdf" in names


@pytest.mark.asyncio
async def test_retrieve_past_citation_pool_skips_when_empty():
    """Empty / None past_citation_ids must not touch the store's batch
    endpoint at all — saves a Qdrant round-trip on a fresh conversation."""
    store = _StubStore([_hit("a.pdf", 0.9)])
    svc = RetrievalService(
        ollama=_StubOllama(),       # type: ignore[arg-type]
        store=store,                # type: ignore[arg-type]
        rerank_fn=lambda **_kw: [0.5],
    )

    await svc.retrieve(
        tenant="t", project="p", question="first turn",
        top_k=5,
        rerank_override=_settings(enabled=False),
        past_citation_ids=None,
    )
    assert store.point_id_calls == []

    await svc.retrieve(
        tenant="t", project="p", question="first turn",
        top_k=5,
        rerank_override=_settings(enabled=False),
        past_citation_ids=[],
    )
    assert store.point_id_calls == []
