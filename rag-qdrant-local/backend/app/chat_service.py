"""Chat orchestration — retrieval, prompt assembly, persistence.

The service deliberately uses **two short-lived DB sessions** per request:

  Phase A (write): create or look up the chat session, commit, close.
  Phase B (no DB): retrieval + LLM generation — can take 30+ seconds.
  Phase C (write): persist user + assistant message, commit, close.

This means an SQLite/Postgres connection is *not* held while the LLM is
generating, which keeps the connection pool usable under concurrent load.
"""

from __future__ import annotations

import json
import re
from typing import Callable, ContextManager, Dict, List, Optional

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from .config import settings
from .database import session_scope
from .models import ChatMessage, ChatSession, Document, TenantProjectPrompt
from .ollama_client import OllamaClient
from .qdrant_store import SearchHit
from .retrieval_service import RetrievalService
from .schemas import ChatResponse, ChatSource
from .utils import deterministic_uuid, new_id

# ---------------------------------------------------------------------------
# Cross-turn citation recall
# ---------------------------------------------------------------------------
# When the user says "the other one you cited" or "and what about that
# document from before", we want the previously-cited chunks back in the
# retrieval candidate pool so the reranker can promote them if still
# relevant. Without this, the fresh-query embedding has no link to past
# context and the right document gets nowhere near top-K.
#
# Tunables, hand-picked rather than configured: 3 turns × 3 chunks max
# bounds the extra pool at 9 candidates, which combined with qdrant_k=12
# fits comfortably under any reasonable reranker batch size. Promote to
# config if a real corpus shows a need to tune.
_CROSS_TURN_RECALL_TURNS = 3
_CROSS_TURN_RECALL_PER_TURN = 3

# Matches an LLM-emitted "Quellen:" trailer that starts its own line. We
# replace it with our deterministic block so the user never sees fabricated
# citations even when the model decided to write its own list. Inline
# mentions ("siehe die Quellen:") aren't matched because they lack the
# trailing newline.
#
# Markdown decorators are tolerated on both sides — qwen2.5 routinely
# writes ``**Quellen:**`` and the older variant left the trailer in. The
# optional groups cover bold (``**`` / ``__``) and ATX heading (``##``)
# prefixes. We don't require open/close to match symmetrically because the
# model is sloppy in practice.
_LLM_SOURCES_TRAILER_RE = re.compile(
    r"\n[ \t]*"
    r"(?:[*_#]+[ \t]*)?"   # optional leading markdown decoration
    r"Quellen:"
    r"(?:[ \t]*[*_]+)?"    # optional trailing **/__
    r"[ \t]*\n"
)

# Corpus-meta questions like "wie viele Dokumente hast du?" do not map onto
# the retrieval pipeline — the LLM hallucinates a count from whatever top-K
# chunks came back. We detect a narrow, high-precision pattern and short-
# circuit to a real count from SQLite instead. Kept short and strict so we
# don't accidentally intercept a real content question like "Welche
# Anweisungen gibt es, wie viele Backups man behalten muss?".
_META_COUNT_RE = re.compile(
    r"\b(?:wie\s*viele?|wieviel(?:e)?|anzahl(?:\s+(?:der|von))?|"
    r"how\s+many|count\s+of)\b[^?]{0,40}\b(?:dokumente?|dateien?|files?|"
    r"documents?|protokolle?|versuche?)\b",
    re.IGNORECASE,
)
_META_COUNT_MAX_LEN = 80


DEFAULT_SYSTEM_PROMPT = (
    "Du bist ein lokaler RAG-Assistent. Beantworte die Frage des Nutzers "
    "ausschließlich auf Basis des bereitgestellten Kontexts. "
    "\n\n"
    "Du DARFST und SOLLST den Kontext zusammenfassen, paraphrasieren, "
    "Zusammenhänge zwischen mehreren Quellen herstellen und logische "
    "Schlüsse aus dem im Kontext belegten Material ziehen. Wörtliche "
    "Übernahme ist nicht erforderlich, solange deine Aussagen durch den "
    "Kontext gestützt sind. "
    "\n\n"
    "Verweigere die Antwort NUR, wenn der Kontext zur konkreten Frage "
    "wirklich keine relevanten Informationen enthält. In diesem Fall "
    "antworte exakt: 'Das steht nicht eindeutig in den bereitgestellten "
    "Dokumenten.' "
    "\n\n"
    "Erfinde keine Fakten, Zahlen, Namen, Paragrafen oder Quellen. "
    "Antworte auf Deutsch. Gib am Ende immer die verwendeten Quellen mit "
    "Datei und — falls vorhanden — Seite oder Sheet/Zeilen aus."
)
# Backwards-compatible alias used by tests + previews. The *runtime* prompt
# may differ if the admin has set an override (see system_prompt_store).
SYSTEM_PROMPT = DEFAULT_SYSTEM_PROMPT

NO_CONTEXT_ANSWER = "Das steht nicht eindeutig in den bereitgestellten Dokumenten."

# Type alias for a "context-managed Session factory" — used as a seam for
# tests to inject a fake session.
SessionFactory = Callable[[], ContextManager[Session]]


def hits_to_sources(hits: List[SearchHit]) -> List[ChatSource]:
    """Map raw Qdrant hits to the public ``ChatSource`` schema.

    Lives at module level so the LLM-less ``/retrieve`` endpoint can reuse
    it without depending on ChatService. ``source_type`` and ``url`` are
    populated when the producing connector stored them in the payload
    (e.g. MediaWiki pages carry their wiki URL).
    """
    out: List[ChatSource] = []
    for h in hits:
        p = h.payload
        out.append(
            ChatSource(
                file_name=p.get("file_name", "?"),
                document_id=p.get("document_id", ""),
                sheet=p.get("sheet"),
                row_start=p.get("row_start"),
                row_end=p.get("row_end"),
                page=p.get("page"),
                chunk_index=int(p.get("chunk_index", 0) or 0),
                score=float(h.score),
                source_type=p.get("source_type"),
                url=p.get("url"),
            )
        )
    return out


class ChatService:
    def __init__(
        self,
        retrieval: Optional[RetrievalService] = None,
        ollama: Optional[OllamaClient] = None,
        session_factory: Optional[SessionFactory] = None,
    ) -> None:
        self.retrieval = retrieval or RetrievalService()
        self.ollama = ollama or OllamaClient()
        # session_scope() is itself a context manager — wrap it once so the
        # factory always returns a *fresh* context per call.
        self.session_factory: SessionFactory = session_factory or session_scope

    # ----- DB phase A: get / create session id (string) --------------------

    def _resolve_session_id(
        self, *, tenant: str, project: str, session_id: Optional[str]
    ) -> str:
        """Return a valid chat-session id, creating one if needed.

        The transaction is opened, committed, and closed inside this call —
        no Session object escapes back to the caller.
        """
        with self.session_factory() as db:
            if session_id:
                existing = db.get(ChatSession, session_id)
                if existing:
                    if existing.tenant != tenant or existing.project != project:
                        raise ValueError(
                            "session_id belongs to a different tenant/project."
                        )
                    return existing.id
            new_session = ChatSession(id=new_id(), tenant=tenant, project=project)
            db.add(new_session)
            # session_scope() commits on exit; capture the id before then so
            # we don't access an attribute on a detached instance later.
            return new_session.id

    # ----- DB lookup: per-collection persona prompt ------------------------

    def _load_persona(self, tenant: str, project: str) -> str:
        """Return the persona prompt for ``(tenant, project)`` or empty string."""
        with self.session_factory() as db:
            row = db.get(TenantProjectPrompt, (tenant, project))
            if row is None:
                return ""
            return (row.persona_prompt or "").strip()

    def _load_chat_model(self, tenant: str, project: str) -> Optional[str]:
        """Return the per-agent chat model override or ``None`` (=use global)."""
        with self.session_factory() as db:
            row = db.get(TenantProjectPrompt, (tenant, project))
            if row is None:
                return None
            value = (row.chat_model or "").strip()
            return value or None

    @staticmethod
    def _compose_system_prompt(
        tenant: str,
        project: str,
        persona: str,
        global_prompt: Optional[str] = None,
    ) -> str:
        """Combine the global anti-hallucination prompt with an optional
        collection-specific persona block.

        The global prompt is always first — persona can extend, never override.
        ``global_prompt`` defaults to the in-code default; the runtime path
        passes the override-resolved value from ``system_prompt_store``.
        """
        base = global_prompt if global_prompt is not None else SYSTEM_PROMPT
        if not persona:
            return base
        return (
            f"{base}\n\n"
            f"---\n"
            f"Profil für die Kollektion '{tenant} / {project}':\n"
            f"{persona}"
        )

    # ----- DB phase B': load recent conversation history ------------------

    def _load_recent_messages(
        self, session_id: str, *, turns: int
    ) -> List[Dict[str, str]]:
        """Return up to ``turns`` past user/assistant *pairs* from this session,
        oldest-first, as ``[{role, content}, ...]``. Excludes the current
        question because we haven't persisted it yet at this point.

        ``turns`` is **pairs** (user + assistant), not individual messages.

        Materialize ``(role, content)`` into plain tuples *inside* the
        session, before the context manager commits + closes. session_scope
        sets ``expire_on_commit=True`` (the default), so reading attributes
        from the ORM instances afterwards would refresh and raise
        ``DetachedInstanceError``. Same pattern as ``apply_overrides``.
        """
        if turns <= 0:
            return []
        # Pull the last 2*turns messages, ordered desc, then flip.
        with self.session_factory() as db:
            rows = list(
                db.execute(
                    select(ChatMessage.role, ChatMessage.content)
                    .where(ChatMessage.session_id == session_id)
                    .order_by(desc(ChatMessage.created_at))
                    .limit(turns * 2)
                ).all()
            )
        rows.reverse()
        return [{"role": role, "content": content} for role, content in rows]

    # ----- meta-question deflection ---------------------------------------

    @staticmethod
    def _is_meta_count_question(question: str) -> bool:
        """True iff the question reads like 'how many documents do you hold?'.

        Length-capped on purpose — anything longer is almost certainly a real
        content question that just happens to mention 'wie viele Dokumente'.
        """
        q = (question or "").strip()
        if not q or len(q) > _META_COUNT_MAX_LEN:
            return False
        return _META_COUNT_RE.search(q) is not None

    def _meta_count_answer(self, tenant: str, project: str) -> str:
        """Build the canned 'I won't guess, here's the real count' reply."""
        with self.session_factory() as db:
            count = (
                db.execute(
                    select(func.count(Document.id)).where(
                        Document.tenant == tenant,
                        Document.project == project,
                    )
                ).scalar()
                or 0
            )
        return (
            f"Im aktuellen Bereich (Tenant: {tenant}, Projekt: {project}) "
            f"sind aktuell {count} Dokumente indiziert.\n\n"
            f"Stelle mir inhaltliche Fragen zu diesen Dokumenten — ich "
            f"suche dann gezielt die passenden Stellen heraus. Eine "
            f"vollständige Liste aller Dateien siehst du im Admin-Bereich "
            f"unter Dokumente."
        )

    def _load_recent_citation_ids(
        self,
        session_id: str,
        *,
        turns: int = _CROSS_TURN_RECALL_TURNS,
        per_turn: int = _CROSS_TURN_RECALL_PER_TURN,
    ) -> List[str]:
        """Collect Qdrant point IDs cited by recent assistant messages.

        Walks the last ``turns`` assistant messages of this session, parses
        each one's ``sources_json`` and reconstructs the Qdrant point id
        from ``(document_id, chunk_index)`` using the same scheme as
        ingestion. Returns deduplicated ids, newest-first. Messages with
        broken/missing sources are skipped silently — we never fail a chat
        request because an old session row has a quirky payload.
        """
        if turns <= 0 or per_turn <= 0:
            return []
        # SELECT only the column we actually need, into plain strings — the
        # ORM-instance attribute path would DetachedInstanceError after the
        # session commits and closes (expire_on_commit=True default).
        with self.session_factory() as db:
            sources_blobs: List[Optional[str]] = list(
                db.execute(
                    select(ChatMessage.sources_json)
                    .where(
                        ChatMessage.session_id == session_id,
                        ChatMessage.role == "assistant",
                    )
                    .order_by(desc(ChatMessage.created_at))
                    .limit(turns)
                ).scalars().all()
            )
        ids: List[str] = []
        seen: set[str] = set()
        for blob in sources_blobs:
            if not blob:
                continue
            try:
                items = json.loads(blob)
            except (TypeError, ValueError):
                continue
            if not isinstance(items, list):
                continue
            count = 0
            for item in items:
                if count >= per_turn:
                    break
                if not isinstance(item, dict):
                    continue
                document_id = str(item.get("document_id") or "")
                chunk_index = item.get("chunk_index")
                if not document_id or chunk_index is None:
                    continue
                pid = deterministic_uuid(document_id, str(chunk_index))
                if pid in seen:
                    continue
                seen.add(pid)
                ids.append(pid)
                count += 1
        return ids

    # ----- DB phase C: append the two messages -----------------------------

    def _persist_messages(
        self,
        *,
        session_id: str,
        question: str,
        answer: str,
        sources: List[ChatSource],
    ) -> None:
        sources_json = json.dumps(
            [s.model_dump() for s in sources], ensure_ascii=False
        )
        with self.session_factory() as db:
            db.add(
                ChatMessage(
                    id=new_id(),
                    session_id=session_id,
                    role="user",
                    content=question,
                )
            )
            db.add(
                ChatMessage(
                    id=new_id(),
                    session_id=session_id,
                    role="assistant",
                    content=answer,
                    sources_json=sources_json,
                )
            )

    # ----- helpers (pure) --------------------------------------------------

    @staticmethod
    def _build_context(hits: List[SearchHit]) -> str:
        blocks = []
        for i, h in enumerate(hits, start=1):
            p = h.payload
            loc_parts = []
            if p.get("page") is not None:
                loc_parts.append(f"Seite {p['page']}")
            if p.get("sheet"):
                loc_parts.append(f"Sheet \"{p['sheet']}\"")
            if p.get("row_start") is not None and p.get("row_end") is not None:
                loc_parts.append(f"Zeilen {p['row_start']}-{p['row_end']}")
            loc = ", ".join(loc_parts) if loc_parts else "—"

            blocks.append(
                f"[Quelle {i}] {p.get('file_name','?')} ({loc}) "
                f"[score={h.score:.3f}]\n{p.get('text','').strip()}"
            )
        return "\n\n".join(blocks)


    @staticmethod
    def _format_sources_block(sources: List[ChatSource]) -> str:
        if not sources:
            return ""
        lines = ["Quellen:"]
        seen = set()
        for s in sources:
            key = (
                s.file_name,
                s.page,
                s.sheet,
                s.row_start,
                s.row_end,
                s.chunk_index,
            )
            if key in seen:
                continue
            seen.add(key)
            parts = [s.file_name]
            if s.page is not None:
                parts.append(f"Seite {s.page}")
            if s.sheet:
                parts.append(f"Sheet \"{s.sheet}\"")
            if s.row_start is not None and s.row_end is not None:
                parts.append(f"Zeilen {s.row_start}-{s.row_end}")
            parts.append(f"Chunk {s.chunk_index}")
            line = "- " + ", ".join(parts)
            # Append a wiki / connector URL when the source carries one so
            # users can click through directly from the chat answer.
            if s.url:
                line = f"{line} ({s.url})"
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _strip_llm_sources_trailer(answer: str) -> str:
        """Drop any ``Quellen:``-prefixed block the LLM tacked onto the answer.

        The LLM occasionally invents citation lines that don't match what we
        actually retrieved (wrong filename, wrong page, wrong "Quelle N:"
        index). We strip it so the deterministic block from
        ``_format_sources_block`` is always the final word. Only the
        trailing block at start-of-line is matched; inline references to
        "Quellen" stay intact.
        """
        m = _LLM_SOURCES_TRAILER_RE.search(answer)
        if m is None:
            return answer
        return answer[: m.start()].rstrip()

    @staticmethod
    def _ensure_sources_appended(answer: str, sources_block: str) -> str:
        if not sources_block:
            return answer
        # Always append our deterministic block, even if the LLM wrote one.
        # The LLM's version can be wrong; ours is always tied to actual hits.
        cleaned = ChatService._strip_llm_sources_trailer(answer)
        return f"{cleaned.rstrip()}\n\n{sources_block}"

    # ----- main entry point ------------------------------------------------

    async def chat(
        self,
        *,
        tenant: str,
        project: str,
        question: str,
        session_id: Optional[str] = None,
        top_k: Optional[int] = None,
    ) -> ChatResponse:
        # Phase A — short-lived DB write
        resolved_session_id = self._resolve_session_id(
            tenant=tenant, project=project, session_id=session_id
        )

        # Meta-question short-circuit: "wie viele Dokumente hast du?" has
        # no honest answer via retrieval — we'd hand the LLM whatever 6
        # random chunks Qdrant returns and it would invent a number. Real
        # count from SQLite is the right answer.
        if self._is_meta_count_question(question):
            answer = self._meta_count_answer(tenant, project)
            self._persist_messages(
                session_id=resolved_session_id,
                question=question,
                answer=answer,
                sources=[],
            )
            return ChatResponse(
                answer=answer, sources=[], session_id=resolved_session_id
            )

        # Cross-turn recall: chunk ids cited in this session's recent
        # assistant messages. The retrieval pipeline folds them into the
        # candidate pool so structurally-referential follow-ups ("the other
        # one you cited") find their target even though the new question
        # doesn't embed near it.
        past_citation_ids = self._load_recent_citation_ids(resolved_session_id)

        # Load history once, before retrieval, so the query-rewriter inside
        # retrieve() can resolve "and what about that?"-style follow-ups.
        # The same history feeds the LLM call further down — no second DB
        # round-trip needed.
        history = self._load_recent_messages(
            resolved_session_id, turns=settings.CHAT_HISTORY_TURNS
        )

        # Phase B — no DB held during this part
        hits = await self.retrieval.retrieve(
            tenant=tenant,
            project=project,
            question=question,
            top_k=top_k,
            past_citation_ids=past_citation_ids,
            history=history,
        )

        if not hits:
            answer = NO_CONTEXT_ANSWER
            self._persist_messages(
                session_id=resolved_session_id,
                question=question,
                answer=answer,
                sources=[],
            )
            return ChatResponse(answer=answer, sources=[], session_id=resolved_session_id)

        context = self._build_context(hits)
        sources = hits_to_sources(hits)
        sources_block = self._format_sources_block(sources)

        user_prompt = (
            f"Kontext:\n{context}\n\n"
            f"Frage: {question}\n\n"
            "Beantworte die Frage gestützt auf den obigen Kontext. Du darfst "
            "zusammenfassen, paraphrasieren und Inhalte mehrerer Quellen "
            "kombinieren — solange deine Aussagen im Kontext belegt sind. "
            "Verweigere nur, wenn der Kontext zur Frage wirklich nichts "
            "Relevantes enthält. Schließe deine Antwort mit einem Abschnitt "
            "'Quellen:' ab, der die verwendeten Dokumente, Seiten/Sheets und "
            "Zeilen auflistet."
        )
        persona = self._load_persona(tenant, project)
        # Local import to avoid circular dependency at module load time.
        from .system_prompt_store import get_system_prompt
        system_message = self._compose_system_prompt(
            tenant, project, persona, global_prompt=get_system_prompt()
        )
        messages = [{"role": "system", "content": system_message}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_prompt})

        chat_model_override = self._load_chat_model(tenant, project)
        raw_answer = await self.ollama.chat(messages, model=chat_model_override)
        answer = self._ensure_sources_appended(raw_answer.strip(), sources_block)

        # Phase C — short-lived DB write again
        self._persist_messages(
            session_id=resolved_session_id,
            question=question,
            answer=answer,
            sources=sources,
        )

        return ChatResponse(
            answer=answer,
            sources=sources,
            session_id=resolved_session_id,
        )
