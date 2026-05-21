"""RetrievalService — conversation-aware query rewriter.

These tests cover the rewriter's plumbing (history trimming, prompt
shape, output cleaning, fallback paths) using a stub Ollama client.
The actual rewrite-quality of the underlying LLM is the eval set's job
(tests/eval/) and is exercised against a live backend; here we just
prove that:

  - empty / disabled paths short-circuit without an Ollama call
  - the rewriter sees a Quellen-stripped history (no filename leakage)
  - cleanly-bracketed model output is unwrapped
  - garbage / overlong / Ollama-failure all fall back to the original
"""

from __future__ import annotations

from typing import List, Optional

import pytest

from app import config as config_module
from app.qdrant_store import SearchHit
from app.rerank_settings import EffectiveRerankSettings
from app.retrieval_service import (
    RetrievalService,
    _clean_rewriter_output,
    _format_rewrite_user_prompt,
    _strip_quellen_for_rewrite,
    _trim_history_for_rewrite,
)


# ---------------------------------------------------------------------------
# Stubs — Ollama + Qdrant
# ---------------------------------------------------------------------------

class _CountingOllama:
    """Captures every chat() call so tests can assert what the rewriter
    sent and inject a canned rewritten string in return."""

    def __init__(self, rewrite_output: str = "REWRITTEN") -> None:
        self.rewrite_output = rewrite_output
        self.chat_calls: List[dict] = []
        self.embed_calls: List[str] = []

    async def chat(self, messages, *, model=None, temperature=None, max_tokens=None):
        self.chat_calls.append({
            "messages": messages, "model": model,
            "temperature": temperature, "max_tokens": max_tokens,
        })
        return self.rewrite_output

    async def embed(self, text: str):
        self.embed_calls.append(text)
        return [0.0] * 4


class _StubStore:
    def __init__(self, hits=None):
        self._hits = hits or []
        self.last_top_k: Optional[int] = None

    def search(self, *, tenant, project, query_vector, top_k, score_threshold=None):
        self.last_top_k = top_k
        return list(self._hits[:top_k])

    def get_points_by_ids(self, *, tenant, project, point_ids):
        return []


def _hit(file_name: str, score: float = 0.9, idx: int = 0) -> SearchHit:
    return SearchHit(
        score=score,
        payload={"file_name": file_name, "document_id": f"doc-{file_name}",
                 "chunk_index": idx, "text": f"body of {file_name}"},
        point_id=f"pt-{file_name}-{idx}",
    )


def _rerank_off() -> EffectiveRerankSettings:
    return EffectiveRerankSettings(
        enabled=False, overfetch_k=0, model="",
        enabled_source="override", overfetch_k_source="override",
        model_source="override", doc_count=0,
    )


# ---------------------------------------------------------------------------
# _strip_quellen_for_rewrite — the leakage guard
# ---------------------------------------------------------------------------

def test_strip_quellen_removes_plain_block():
    msg = "Eine Antwort.\n\nQuellen:\n- foo.pdf, Seite 1\n"
    assert _strip_quellen_for_rewrite(msg) == "Eine Antwort."


def test_strip_quellen_removes_bold_label():
    msg = "Body.\n\n**Quellen:**\n- foo.pdf\n"
    assert _strip_quellen_for_rewrite(msg) == "Body."


def test_strip_quellen_keeps_message_without_trailer():
    msg = "Eine Antwort ohne Quellenangabe."
    assert _strip_quellen_for_rewrite(msg) == msg


# ---------------------------------------------------------------------------
# _trim_history_for_rewrite — short window + cleaned content
# ---------------------------------------------------------------------------

def test_trim_history_keeps_last_two_pairs():
    history = [
        {"role": "user", "content": "Q1"},
        {"role": "assistant", "content": "A1"},
        {"role": "user", "content": "Q2"},
        {"role": "assistant", "content": "A2"},
        {"role": "user", "content": "Q3"},
        {"role": "assistant", "content": "A3"},
    ]
    out = _trim_history_for_rewrite(history, turns=2)
    # Last 4 messages: Q2/A2/Q3/A3
    assert [m["content"] for m in out] == ["Q2", "A2", "Q3", "A3"]


def test_trim_history_strips_quellen_from_assistant_turns():
    history = [
        {"role": "user", "content": "Q"},
        {"role": "assistant", "content": "Antwort.\n\nQuellen:\n- secret.pdf"},
    ]
    out = _trim_history_for_rewrite(history)
    # Assistant content must NOT carry the filename through to the rewriter.
    assert "secret.pdf" not in out[1]["content"]
    assert out[1]["content"] == "Antwort."


def test_trim_history_returns_empty_for_none_or_empty():
    assert _trim_history_for_rewrite(None) == []
    assert _trim_history_for_rewrite([]) == []


# ---------------------------------------------------------------------------
# _format_rewrite_user_prompt — shape the rewriter sees
# ---------------------------------------------------------------------------

def test_format_rewrite_user_prompt_uses_german_labels():
    history = [
        {"role": "user", "content": "Frage eins"},
        {"role": "assistant", "content": "Antwort eins"},
    ]
    out = _format_rewrite_user_prompt(history, "Welche von beiden?")
    # Speaker labels in German so the rewriter doesn't get confused.
    assert "Nutzer: Frage eins" in out
    assert "Assistent: Antwort eins" in out
    assert "Aktuelle Frage: Welche von beiden?" in out


# ---------------------------------------------------------------------------
# _clean_rewriter_output — unwrap common LLM tics
# ---------------------------------------------------------------------------

def test_clean_strips_surrounding_quotes():
    assert _clean_rewriter_output('"Welche Backup-Frequenz?"') == "Welche Backup-Frequenz?"
    assert _clean_rewriter_output("'foo bar'") == "foo bar"
    assert _clean_rewriter_output("„foo bar“") == "foo bar"


def test_clean_strips_labeled_prefix():
    assert _clean_rewriter_output("Frage: Wie spät?") == "Wie spät?"
    assert _clean_rewriter_output("Umformulierte Frage: Was?") == "Was?"


def test_clean_empty_input_stays_empty():
    assert _clean_rewriter_output("") == ""
    assert _clean_rewriter_output("   ") == ""


# ---------------------------------------------------------------------------
# _rewrite_if_followup — the orchestrator
# ---------------------------------------------------------------------------

@pytest.fixture
def enable_rewrite(monkeypatch):
    """Force ENABLE_QUERY_REWRITE on regardless of env."""
    monkeypatch.setattr(config_module.settings, "ENABLE_QUERY_REWRITE", True)
    monkeypatch.setattr(config_module.settings, "REWRITE_MODEL", "")
    yield


@pytest.fixture
def disable_rewrite(monkeypatch):
    monkeypatch.setattr(config_module.settings, "ENABLE_QUERY_REWRITE", False)
    yield


@pytest.mark.asyncio
async def test_rewrite_short_circuits_when_disabled(disable_rewrite):
    ollama = _CountingOllama()
    svc = RetrievalService(ollama=ollama, store=_StubStore())  # type: ignore[arg-type]
    out = await svc._rewrite_if_followup(
        "und welche pulver?", history=[{"role": "user", "content": "Q1"}],
    )
    assert out == "und welche pulver?"
    assert ollama.chat_calls == []


@pytest.mark.asyncio
async def test_rewrite_short_circuits_on_empty_history(enable_rewrite):
    ollama = _CountingOllama()
    svc = RetrievalService(ollama=ollama, store=_StubStore())  # type: ignore[arg-type]
    out = await svc._rewrite_if_followup("Frische Frage", history=None)
    assert out == "Frische Frage"
    assert ollama.chat_calls == []


@pytest.mark.asyncio
async def test_rewrite_returns_cleaned_llm_output(enable_rewrite):
    ollama = _CountingOllama(rewrite_output='"Welche Pulver kamen bei Versuch 905 zum Einsatz?"')
    svc = RetrievalService(ollama=ollama, store=_StubStore())  # type: ignore[arg-type]
    out = await svc._rewrite_if_followup(
        "und welche pulver?",
        history=[
            {"role": "user", "content": "Erzähle mir von Versuch 905."},
            {"role": "assistant", "content": "Versuch 905 nutzte Aggregat AM400."},
        ],
    )
    assert out == "Welche Pulver kamen bei Versuch 905 zum Einsatz?"
    assert len(ollama.chat_calls) == 1
    # Rewriter is called with deterministic temperature.
    assert ollama.chat_calls[0]["temperature"] == 0.0


@pytest.mark.asyncio
async def test_rewrite_falls_back_to_original_on_ollama_error(enable_rewrite):
    class _Boom:
        async def chat(self, *_a, **_k):
            from app.ollama_client import OllamaError
            raise OllamaError("model OOM")
        async def embed(self, _t):
            return [0.0]
    svc = RetrievalService(ollama=_Boom(), store=_StubStore())  # type: ignore[arg-type]
    out = await svc._rewrite_if_followup(
        "und welche pulver?",
        history=[{"role": "user", "content": "Q"}, {"role": "assistant", "content": "A"}],
    )
    assert out == "und welche pulver?"


@pytest.mark.asyncio
async def test_rewrite_falls_back_on_empty_llm_output(enable_rewrite):
    ollama = _CountingOllama(rewrite_output="   ")
    svc = RetrievalService(ollama=ollama, store=_StubStore())  # type: ignore[arg-type]
    out = await svc._rewrite_if_followup(
        "und welche pulver?",
        history=[{"role": "user", "content": "Q"}, {"role": "assistant", "content": "A"}],
    )
    assert out == "und welche pulver?"


@pytest.mark.asyncio
async def test_rewrite_falls_back_on_overlong_llm_output(enable_rewrite):
    # Some LLMs ignore "answer only with the question" and dump a paragraph.
    # Treat anything over the max as nonsense.
    ollama = _CountingOllama(rewrite_output="x" * 5000)
    svc = RetrievalService(ollama=ollama, store=_StubStore())  # type: ignore[arg-type]
    out = await svc._rewrite_if_followup(
        "und welche pulver?",
        history=[{"role": "user", "content": "Q"}, {"role": "assistant", "content": "A"}],
    )
    assert out == "und welche pulver?"


@pytest.mark.asyncio
async def test_rewrite_passes_model_override_when_configured(monkeypatch, enable_rewrite):
    monkeypatch.setattr(config_module.settings, "REWRITE_MODEL", "qwen2.5:7b")
    ollama = _CountingOllama(rewrite_output="rewritten")
    svc = RetrievalService(ollama=ollama, store=_StubStore())  # type: ignore[arg-type]
    await svc._rewrite_if_followup(
        "und?",
        history=[{"role": "user", "content": "Q"}, {"role": "assistant", "content": "A"}],
    )
    assert ollama.chat_calls[0]["model"] == "qwen2.5:7b"


# ---------------------------------------------------------------------------
# Integration through retrieve() — rewriter feeds the embedder
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retrieve_embeds_rewritten_form_when_history_present(enable_rewrite):
    ollama = _CountingOllama(rewrite_output="Welche Pulver bei Lubrizol AM 400?")
    store = _StubStore([_hit("Lubrizol_AM400_versuch.pdf")])
    svc = RetrievalService(
        ollama=ollama, store=store,  # type: ignore[arg-type]
        rerank_fn=lambda **_kw: pytest.fail("rerank disabled"),  # type: ignore[arg-type]
    )
    await svc.retrieve(
        tenant="t", project="p",
        question="und welche pulver?",
        history=[
            {"role": "user", "content": "Erzähle von Lubrizol AM 400 Versuchen."},
            {"role": "assistant", "content": "Es gibt drei Versuche."},
        ],
        top_k=3,
        rerank_override=_rerank_off(),
    )
    # The embed call must have received the *rewritten* question, not the
    # original — that's what makes follow-ups land on the right chunks.
    assert any("Lubrizol" in e for e in ollama.embed_calls)


@pytest.mark.asyncio
async def test_retrieve_embeds_original_when_history_absent(enable_rewrite):
    ollama = _CountingOllama(rewrite_output="REWRITTEN-but-must-not-fire")
    store = _StubStore([_hit("doc.pdf")])
    svc = RetrievalService(
        ollama=ollama, store=store,  # type: ignore[arg-type]
        rerank_fn=lambda **_kw: pytest.fail("rerank disabled"),  # type: ignore[arg-type]
    )
    await svc.retrieve(
        tenant="t", project="p",
        question="Fresh standalone question?",
        history=None,
        top_k=3,
        rerank_override=_rerank_off(),
    )
    # No rewriter call when history is empty.
    assert ollama.chat_calls == []
    # Embedder saw the original.
    assert ollama.embed_calls == ["Fresh standalone question?"]
