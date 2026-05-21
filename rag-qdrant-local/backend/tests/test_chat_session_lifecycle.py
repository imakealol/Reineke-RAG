"""Regression: chat history helpers must not DetachedInstance after commit.

Both ``_load_recent_messages`` and ``_load_recent_citation_ids`` are called
*after* ``session_scope`` has commit-and-closed an earlier write. With
``expire_on_commit=True`` (SQLAlchemy default), the ORM attributes on rows
fetched in a follow-up read would refresh on first access — but the
session has already closed by the time we iterate the rows, so the
refresh raises ``DetachedInstanceError`` and crashes the whole /chat
request with a bare 500.

The fix is to SELECT scalar columns rather than entire ORM instances so
nothing needs to be refreshed. This test sets up a real (in-memory)
SQLAlchemy engine and proves the helpers survive the close.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.chat_service import ChatService
from app.models import Base, ChatMessage, ChatSession
from app.utils import deterministic_uuid, new_id


@pytest.fixture()
def session_factory_real_sqlite():
    """Return a session_factory that mirrors ``session_scope``'s contract:
    commit + close on normal exit. Backed by a real in-memory SQLite
    engine so we exercise the expire-on-commit + close lifecycle for real.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    @contextmanager
    def factory() -> Iterator[Session]:
        s = Local()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    return factory


def _seed_session_with_messages(factory, *, session_id: str, sources_json: str):
    with factory() as db:
        db.add(ChatSession(id=session_id, tenant="t", project="p"))
        db.add(ChatMessage(id=new_id(), session_id=session_id, role="user", content="frage"))
        db.add(
            ChatMessage(
                id=new_id(),
                session_id=session_id,
                role="assistant",
                content="antwort",
                sources_json=sources_json,
            )
        )


def test_load_recent_citation_ids_survives_session_close(session_factory_real_sqlite):
    """The exact production failure from PR #19 bundle: turn 2 of a chat
    crashed with DetachedInstanceError because the helper accessed
    ``row.sources_json`` after ``session_scope`` had committed + closed.
    Selecting only the column avoids the refresh entirely.
    """
    sid = "regression-session"
    sources_json = json.dumps([
        {"document_id": "doc-A", "chunk_index": 0},
        {"document_id": "doc-B", "chunk_index": 3},
    ])
    _seed_session_with_messages(
        session_factory_real_sqlite, session_id=sid, sources_json=sources_json,
    )

    svc = ChatService(session_factory=session_factory_real_sqlite)
    out = svc._load_recent_citation_ids(sid)
    assert out == [
        deterministic_uuid("doc-A", "0"),
        deterministic_uuid("doc-B", "3"),
    ]


def test_load_recent_messages_survives_session_close(session_factory_real_sqlite):
    """Sibling regression — ``_load_recent_messages`` had the same latent
    bug because it returned a list comprehension over expired ORM rows
    after the with-block exited. Production never hit it because OpenWebUI
    rarely tipped past 1-turn at install time; under multi-turn use it
    would crash identically.
    """
    sid = "regression-session-msg"
    _seed_session_with_messages(
        session_factory_real_sqlite, session_id=sid, sources_json="[]",
    )
    svc = ChatService(session_factory=session_factory_real_sqlite)
    history = svc._load_recent_messages(sid, turns=2)
    # Oldest-first ordering: user then assistant.
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert [m["content"] for m in history] == ["frage", "antwort"]
