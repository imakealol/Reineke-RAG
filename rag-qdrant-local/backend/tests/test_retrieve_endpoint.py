"""``/retrieve`` is the LLM-less retrieval endpoint used by the eval runner.

It must:

* delegate to the same RetrievalService as ``/chat``,
* never invoke the LLM (no ``ollama.chat`` call),
* surface ``QdrantStoreError`` from the tenant/project guard as a 500.
"""

from __future__ import annotations

import asyncio
from typing import List

import pytest

from app.chat_service import ChatService, hits_to_sources
from app.qdrant_store import SearchHit


class _StubRetrieval:
    """Records each retrieve() call and returns a fixed result."""

    def __init__(self, hits: List[SearchHit]) -> None:
        self._hits = hits
        self.calls: list[dict] = []

    async def retrieve(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return list(self._hits)


class _ExplodingOllama:
    async def chat(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("/retrieve must not invoke the LLM")

    async def embed(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError(
            "/retrieve must not embed at the endpoint layer; embedding "
            "belongs inside RetrievalService.retrieve()"
        )


def _make_hit(file_name: str, score: float, **payload) -> SearchHit:
    base = {
        "file_name": file_name,
        "document_id": f"doc-{file_name}",
        "chunk_index": 0,
    }
    base.update(payload)
    return SearchHit(score=score, payload=base, point_id=f"pt-{file_name}")


@pytest.mark.asyncio
async def test_retrieve_returns_sources_without_llm():
    from app.main import retrieve_endpoint
    from app.schemas import RetrieveRequest

    hits = [
        _make_hit("PL.ISMS007_Kennwort_Richtlinie.docx", 0.81, page=2),
        _make_hit("PL.ISMS010_Backup-Richtlinie.docx", 0.74, page=1),
    ]
    retrieval = _StubRetrieval(hits)
    svc = ChatService(
        retrieval=retrieval,  # type: ignore[arg-type]
        ollama=_ExplodingOllama(),  # type: ignore[arg-type]
    )

    req = RetrieveRequest(tenant="t", project="p", question="Was sagt die Kennwort-Richtlinie?")
    resp = await retrieve_endpoint(req, svc=svc)

    assert len(resp.sources) == 2
    assert resp.sources[0].file_name == "PL.ISMS007_Kennwort_Richtlinie.docx"
    assert resp.sources[0].score == pytest.approx(0.81)
    # Top-k threaded through unchanged when omitted; history defaults to None.
    assert retrieval.calls == [
        {
            "tenant": "t", "project": "p",
            "question": "Was sagt die Kennwort-Richtlinie?",
            "top_k": None, "history": None,
        }
    ]


def test_retrieve_runs_under_sync_pytest():
    asyncio.run(test_retrieve_returns_sources_without_llm())


@pytest.mark.asyncio
async def test_retrieve_passes_top_k_override():
    from app.main import retrieve_endpoint
    from app.schemas import RetrieveRequest

    retrieval = _StubRetrieval([])
    svc = ChatService(retrieval=retrieval, ollama=_ExplodingOllama())  # type: ignore[arg-type]

    req = RetrieveRequest(tenant="t", project="p", question="x", top_k=12)
    await retrieve_endpoint(req, svc=svc)

    assert retrieval.calls[0]["top_k"] == 12


@pytest.mark.asyncio
async def test_retrieve_threads_history_into_retrieval_call():
    """The /retrieve endpoint must forward the history list to the
    retrieval layer so the eval harness can exercise the query-rewriter
    without going through /chat + SQLite persistence."""
    from app.main import retrieve_endpoint
    from app.schemas import HistoryTurn, RetrieveRequest

    retrieval = _StubRetrieval([])
    svc = ChatService(retrieval=retrieval, ollama=_ExplodingOllama())  # type: ignore[arg-type]

    req = RetrieveRequest(
        tenant="t", project="p", question="und welche?",
        history=[
            HistoryTurn(role="user", content="Welche Backup-Frequenz?"),
            HistoryTurn(role="assistant", content="Täglich, wöchentlich, monatlich."),
        ],
    )
    await retrieve_endpoint(req, svc=svc)

    forwarded = retrieval.calls[0]["history"]
    assert forwarded == [
        {"role": "user", "content": "Welche Backup-Frequenz?"},
        {"role": "assistant", "content": "Täglich, wöchentlich, monatlich."},
    ]


def test_hits_to_sources_maps_payload_fields():
    hits = [
        _make_hit(
            "Plasmatreat Maßnahmenplan USV.xlsx",
            0.62,
            sheet="Maßnahmen",
            row_start=2,
            row_end=11,
            chunk_index=3,
        )
    ]

    sources = hits_to_sources(hits)
    assert len(sources) == 1
    s = sources[0]
    assert s.file_name == "Plasmatreat Maßnahmenplan USV.xlsx"
    assert s.sheet == "Maßnahmen"
    assert s.row_start == 2 and s.row_end == 11
    assert s.chunk_index == 3
    assert s.score == pytest.approx(0.62)


def test_hits_to_sources_tolerates_missing_optional_fields():
    """Older points may lack sheet/row metadata — mapping should not crash."""
    hit = SearchHit(
        score=0.5,
        payload={"file_name": "x.pdf", "document_id": "d"},
        point_id="p",
    )
    sources = hits_to_sources([hit])
    assert sources[0].file_name == "x.pdf"
    assert sources[0].chunk_index == 0
    assert sources[0].sheet is None
