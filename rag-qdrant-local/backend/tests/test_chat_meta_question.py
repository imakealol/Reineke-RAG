"""ChatService — corpus meta-question deflection.

"Wie viele Dokumente hast du?" is structurally not answerable via
retrieval — the LLM would hallucinate a count from whatever 6 random
chunks Qdrant returned. We detect a narrow set of these meta-questions
and short-circuit with a real count from SQLite.

Tests cover both the matcher's positive cases and (importantly) the
negatives where a content question incidentally mentions a count.
"""

from __future__ import annotations

from typing import Optional

import pytest

from app.chat_service import ChatService


# ---------------------------------------------------------------------------
# Positive matches — these MUST trigger the short-circuit
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "question",
    [
        "wie viele dokumente hast du?",
        "Wie viele Dokumente sind im Index?",
        "wieviele dateien sind das?",
        "Anzahl der Dokumente?",
        "anzahl von dateien",
        "how many documents do you hold",
        "How many files are indexed?",
        "wie viele protokolle hast du?",
        "Wie viele Versuche sind drin",
    ],
)
def test_meta_count_detector_fires_on_canonical_phrasings(question: str):
    assert ChatService._is_meta_count_question(question) is True


# ---------------------------------------------------------------------------
# Negative matches — content questions that incidentally use count words
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "question",
    [
        # Real content question that mentions backups, not asking how many
        # the system knows.
        "Welche Anweisungen gibt es, wie viele Backups man behalten muss?",
        # Content question about a specific document's count semantics.
        "Wie viele Zeichen muss ein Kennwort mindestens haben?",
        # Topic question that mentions Dokumente as part of a sentence.
        "Welche Klassifizierung gilt für interne Dokumente nach ISMS015?",
        # Long question — even if it pattern-matches, the length cap kicks in.
        "Wie viele Dokumente muss ein Mitarbeiter laut ISMS015 nach Ablauf "
        "der Aufbewahrungsfrist regelmäßig kontrolliert vernichten lassen?",
        # Empty / whitespace.
        "",
        "   ",
    ],
)
def test_meta_count_detector_does_not_fire_on_content_questions(question: str):
    assert ChatService._is_meta_count_question(question) is False


# ---------------------------------------------------------------------------
# _meta_count_answer — uses the injected session_factory to count
# ---------------------------------------------------------------------------

class _StubSession:
    """Tiny stand-in for a SQLAlchemy Session that returns a canned count."""

    def __init__(self, count: int) -> None:
        self._count = count

    def execute(self, _stmt):
        return _StubResult(self._count)


class _StubResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


def _factory_for(count: Optional[int]):
    from contextlib import contextmanager

    @contextmanager
    def factory():
        yield _StubSession(count if count is not None else 0)

    return factory


def test_meta_count_answer_includes_real_count_and_tenant_project():
    svc = ChatService(session_factory=_factory_for(42))  # type: ignore[arg-type]
    out = svc._meta_count_answer(tenant="reineke", project="watch")
    assert "42" in out
    assert "reineke" in out and "watch" in out
    # The phrasing nudges the user toward content questions instead of
    # corpus-meta ones — that's the whole UX point of the deflection.
    assert "inhaltliche" in out.lower() or "passenden stellen" in out.lower()


def test_meta_count_answer_handles_empty_collection():
    svc = ChatService(session_factory=_factory_for(0))  # type: ignore[arg-type]
    out = svc._meta_count_answer(tenant="t", project="p")
    assert "0 Dokumente" in out
