"""ChatService — recover Qdrant point ids cited in recent assistant turns.

The recall helper feeds previously-cited chunks back into the retrieval
candidate pool so structurally-referential follow-ups ("the other one you
cited earlier") have their target still in scope. These tests verify the
SQLite walk + point-id reconstruction without touching Qdrant.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Iterator, List, Optional

from app.chat_service import ChatService
from app.utils import deterministic_uuid


# ---------------------------------------------------------------------------
# Stub Session + factory — capture the executed query and feed back rows.
# ---------------------------------------------------------------------------

class _Row:
    def __init__(self, role: str, content: str, sources_json: Optional[str]) -> None:
        self.role = role
        self.content = content
        self.sources_json = sources_json


class _Result:
    def __init__(self, rows: List[_Row]) -> None:
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _StubSession:
    def __init__(self, rows: List[_Row]) -> None:
        self._rows = rows

    def execute(self, _stmt):
        return _Result(self._rows)


def _factory_for(rows: List[_Row]):
    @contextmanager
    def factory() -> Iterator[_StubSession]:
        yield _StubSession(rows)

    return factory


def _src(document_id: str, chunk_index: int) -> dict:
    """Minimal ChatSource shape that _load_recent_citation_ids needs."""
    return {"document_id": document_id, "chunk_index": chunk_index}


# ---------------------------------------------------------------------------
# Empty / zero-turn cases short-circuit without DB load
# ---------------------------------------------------------------------------

def test_recall_returns_empty_when_turns_zero():
    svc = ChatService(session_factory=_factory_for([]))  # type: ignore[arg-type]
    assert svc._load_recent_citation_ids("sess", turns=0) == []


def test_recall_returns_empty_when_per_turn_zero():
    svc = ChatService(session_factory=_factory_for([_Row("assistant", "x", "[]")]))  # type: ignore[arg-type]
    assert svc._load_recent_citation_ids("sess", per_turn=0) == []


def test_recall_returns_empty_when_no_assistant_rows():
    svc = ChatService(session_factory=_factory_for([]))  # type: ignore[arg-type]
    assert svc._load_recent_citation_ids("sess") == []


# ---------------------------------------------------------------------------
# Reconstructed ids match the ingest scheme
# ---------------------------------------------------------------------------

def test_recall_reconstructs_point_id_from_document_id_and_chunk_index():
    rows = [
        _Row(
            "assistant",
            "Antwort.",
            json.dumps([_src("doc-A", 3)]),
        )
    ]
    svc = ChatService(session_factory=_factory_for(rows))  # type: ignore[arg-type]
    out = svc._load_recent_citation_ids("sess")
    # Must match exactly what ingestion_service writes for (doc-A, chunk 3).
    assert out == [deterministic_uuid("doc-A", "3")]


def test_recall_caps_at_per_turn_per_assistant_message():
    rows = [
        _Row(
            "assistant",
            "Antwort mit vielen Quellen.",
            json.dumps([_src("doc-A", i) for i in range(10)]),
        )
    ]
    svc = ChatService(session_factory=_factory_for(rows))  # type: ignore[arg-type]
    out = svc._load_recent_citation_ids("sess", per_turn=2)
    # Only the first two citations from the row contribute.
    assert out == [
        deterministic_uuid("doc-A", "0"),
        deterministic_uuid("doc-A", "1"),
    ]


def test_recall_dedupes_when_same_chunk_cited_in_multiple_turns():
    same_src = json.dumps([_src("doc-A", 0)])
    rows = [
        _Row("assistant", "t3", same_src),  # newest
        _Row("assistant", "t2", same_src),
        _Row("assistant", "t1", same_src),  # oldest
    ]
    svc = ChatService(session_factory=_factory_for(rows))  # type: ignore[arg-type]
    out = svc._load_recent_citation_ids("sess", turns=3, per_turn=2)
    assert out == [deterministic_uuid("doc-A", "0")]


# ---------------------------------------------------------------------------
# Defensive parsing — malformed sources_json must not break a chat request
# ---------------------------------------------------------------------------

def test_recall_skips_rows_with_unparseable_sources_json():
    rows = [
        _Row("assistant", "bad", "not json {"),
        _Row("assistant", "good", json.dumps([_src("doc-A", 0)])),
    ]
    svc = ChatService(session_factory=_factory_for(rows))  # type: ignore[arg-type]
    out = svc._load_recent_citation_ids("sess")
    assert out == [deterministic_uuid("doc-A", "0")]


def test_recall_skips_items_without_document_id_or_chunk_index():
    bad_payload = json.dumps([
        {"document_id": "", "chunk_index": 0},     # empty doc id
        {"document_id": "doc-X"},                  # missing chunk_index
        {"chunk_index": 5},                        # missing document_id
        {"document_id": "doc-Y", "chunk_index": 1},  # valid
    ])
    rows = [_Row("assistant", "x", bad_payload)]
    svc = ChatService(session_factory=_factory_for(rows))  # type: ignore[arg-type]
    out = svc._load_recent_citation_ids("sess")
    assert out == [deterministic_uuid("doc-Y", "1")]


def test_recall_handles_sources_json_being_not_a_list():
    rows = [_Row("assistant", "x", json.dumps({"document_id": "x", "chunk_index": 0}))]
    svc = ChatService(session_factory=_factory_for(rows))  # type: ignore[arg-type]
    assert svc._load_recent_citation_ids("sess") == []
