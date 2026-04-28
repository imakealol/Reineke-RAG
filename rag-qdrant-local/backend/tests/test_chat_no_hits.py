"""When retrieval returns nothing, the chat service must reply with the
fallback string and never call Ollama for generation."""

import asyncio
from contextlib import contextmanager
from typing import List

import pytest

from app.chat_service import NO_CONTEXT_ANSWER, ChatService


class _NoOpRetrieval:
    async def retrieve(self, **_kwargs):  # type: ignore[no-untyped-def]
        return []


class _ExplodingOllama:
    async def chat(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("chat() must not be called when there are no hits")


class _StubSession:
    """Minimal duck-typed Session — collects writes, ignores everything else."""

    def __init__(self) -> None:
        self.added: List = []

    def add(self, obj):  # type: ignore[no-untyped-def]
        self.added.append(obj)

    def get(self, _model, _id):  # type: ignore[no-untyped-def]
        return None

    def flush(self):
        pass

    def commit(self):
        pass


@contextmanager
def _stub_session_factory():
    yield _StubSession()


@pytest.mark.asyncio
async def test_chat_returns_fallback_without_hits():
    svc = ChatService(
        retrieval=_NoOpRetrieval(),
        ollama=_ExplodingOllama(),  # type: ignore[arg-type]
        session_factory=_stub_session_factory,
    )

    resp = await svc.chat(
        tenant="t",
        project="p",
        question="Welche Server sind im Inventar?",
    )

    assert resp.answer == NO_CONTEXT_ANSWER
    assert resp.sources == []
    assert resp.session_id


def test_sync_runner():
    """Allow `pytest` to discover this even without --asyncio-mode auto."""
    asyncio.run(test_chat_returns_fallback_without_hits())
