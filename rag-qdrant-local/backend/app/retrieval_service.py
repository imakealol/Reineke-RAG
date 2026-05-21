"""Retrieval = (optional) query-rewrite → embed → Qdrant search (tenant+
project filter) → optional cross-encoder rerank → stem-dedup → top-K."""

from __future__ import annotations

import asyncio
import os
import re
from typing import Callable, ContextManager, Dict, List, Optional

from sqlalchemy.orm import Session

from . import rerank_settings as rerank_settings_module
from .config import settings
from .database import session_scope
from .ollama_client import OllamaClient, OllamaError
from .qdrant_store import QdrantStore, SearchHit
from .rerank_settings import EffectiveRerankSettings
from .utils import get_logger

log = get_logger(__name__)

SessionFactory = Callable[[], ContextManager[Session]]


# ---------------------------------------------------------------------------
# Stem-based deduplication
# ---------------------------------------------------------------------------
# Customers commonly have both the source DOCX and an exported PDF of the
# same document. Without dedup, both win retrieval slots for the same
# content, starving genuinely-different documents out of the top-K. We
# collapse by filename stem so each *logical* document gets at most one
# slot at this stage; finer-grained chunk dedup inside a document (page 2
# vs page 6 of the same PDF) stays — that's signal, not noise.
#
# Stem-stripping rules mirror the chunker so the join is symmetric:
# strip extension, strip ISO date suffix (_20251231), strip version suffix
# (_v2 / -v10). Two passes for names carrying both.

_FILENAME_DATE_SUFFIX_RE = re.compile(r"_\d{8}$")
_FILENAME_VERSION_SUFFIX_RE = re.compile(r"[_\-]v\d+$")


def _document_stem(file_name: str) -> str:
    """Return the comparison key for stem-dedup.

    Case-insensitive so ``Foo.PDF`` and ``foo.pdf`` collapse. Extension and
    bookkeeping suffixes (``_20251231``, ``_v2``) are stripped. Two passes
    in case a name carries both. Empty input returns empty string.
    """
    if not file_name:
        return ""
    stem, _ = os.path.splitext(file_name)
    stem = _FILENAME_DATE_SUFFIX_RE.sub("", stem)
    stem = _FILENAME_VERSION_SUFFIX_RE.sub("", stem)
    return stem.lower()


def _dedup_by_stem(hits: List[SearchHit]) -> List[SearchHit]:
    """Keep the highest-scoring hit per filename stem, preserving order.

    Hits without a ``file_name`` payload (rare, defensive) bypass dedup so a
    payload accident never silently drops them. Stems that resolve to empty
    also bypass — better to keep the hit than to merge unrelated documents.
    """
    seen: set[str] = set()
    out: List[SearchHit] = []
    for h in hits:
        file_name = str((h.payload or {}).get("file_name") or "")
        stem = _document_stem(file_name)
        if not stem:
            out.append(h)
            continue
        if stem in seen:
            continue
        seen.add(stem)
        out.append(h)
    return out


def _merge_unique_by_point_id(
    primary: List[SearchHit], extras: List[SearchHit]
) -> List[SearchHit]:
    """Append ``extras`` after ``primary``, skipping duplicates by point_id.

    Used to fold past-citation candidates into the fresh retrieval set
    without inflating the candidate pool when the same chunk shows up in
    both. ``primary`` order is preserved so the reranker's input still
    starts with the highest-confidence fresh candidates.
    """
    seen = {h.point_id for h in primary}
    merged = list(primary)
    for h in extras:
        if h.point_id in seen:
            continue
        seen.add(h.point_id)
        merged.append(h)
    return merged


# ---------------------------------------------------------------------------
# Query-time synonym expansion
# ---------------------------------------------------------------------------
# Some DACH/IT terms split into near-synonyms where the corpus and the user
# language don't always overlap. The eval set surfaced exactly one such gap
# (q09: "externe Dienstleister" → expected file is PL.ISMS017_…_Lieferanten),
# and bge-m3 alone doesn't bridge the two. Rather than rewriting via the LLM
# (slow, adds latency budget back to the loop) or pulling in a dictionary
# package (extra dep, mostly unused), we maintain a tiny hand-curated table
# of pairs that the eval has shown to matter.
#
# Format: trigger token → form to append. Case-insensitive substring match
# on whole words only. Bidirectional pairs declared explicitly so the
# behaviour is obvious — adding a pair never silently changes another.
#
# Expansion appends the synonym in parentheses after the original question:
#   "...externe Dienstleister?"  ⇒  "...externe Dienstleister? (Lieferanten)"
# A single embedding then covers both terms, no extra Qdrant call needed.

# Each entry is (stem, addition). The stem matches at the start of any
# word (``\b<stem>``) so all declensions and German compounds are caught:
# ``dienstleister`` matches Dienstleister/Dienstleistern/Dienstleister-Audit,
# but not Dienstleistung. Keep stems strict; broaden only when the eval
# proves a gap.
_QUERY_SYNONYMS: List[tuple[str, str]] = [
    # ISMS017 lives under "Lieferanten"; users ask in "Dienstleister".
    ("dienstleister", "Lieferanten"),
    ("lieferant", "Dienstleister"),
]


def _expand_query_with_synonyms(question: str) -> str:
    """Return ``question`` with any matching synonym(s) appended in
    parentheses. Idempotent — re-running on an already-expanded question
    is a no-op because the synonym is now part of the string."""
    if not question:
        return question
    lower = question.lower()
    additions: List[str] = []
    seen_lower = set()
    for trigger, addition in _QUERY_SYNONYMS:
        # Prefix at a word boundary — matches German declensions/compounds
        # like Lieferantenliste while skipping Lieferung / Dienstleistung.
        if not re.search(rf"\b{re.escape(trigger)}", lower):
            continue
        addition_lower = addition.lower()
        # Skip if the addition is already present, or queued from a
        # previous trigger in this loop.
        if addition_lower in lower or addition_lower in seen_lower:
            continue
        additions.append(addition)
        seen_lower.add(addition_lower)
    if not additions:
        return question
    return f"{question} ({', '.join(additions)})"


# ---------------------------------------------------------------------------
# Conversation-aware query rewriting
# ---------------------------------------------------------------------------
# Without rewriting, an abstract follow-up like "welche von beiden ist
# strenger?" embeds six bare words — Qdrant returns random "strenger
# Anforderungen"-flavoured chunks unrelated to the two topics under
# discussion. With rewriting, the same question becomes "Welche hat
# strengere Anforderungen — die Backup-Richtlinie oder die Kennwort-
# Richtlinie?" which anchors retrieval correctly.
#
# Trade-off: one extra LLM call per follow-up turn (~0.3-1.5s with a 7B
# rewriter). Disabled wholesale via ENABLE_QUERY_REWRITE = False if it
# misbehaves; falls back to the original question on any error.

REWRITE_SYSTEM_PROMPT = (
    "Du formulierst Folgefragen in einem RAG-Chat zu selbsterklärenden "
    "Einzelfragen um. Lies die bisherige Konversation und die aktuelle "
    "Frage. Gib die Frage so zurück, dass sie auch ohne die Konversation "
    "verständlich ist — Bezugspronomen (er, sie, das, dort, jene) "
    "aufgelöst, fehlende Entitäten ergänzt.\n\n"
    "WICHTIGE REGELN:\n"
    "• Wenn die Frage bereits selbsterklärend ist, gib sie UNVERÄNDERT zurück.\n"
    "• Wenn die Frage ein neues Thema öffnet (Themenwechsel weg vom "
    "bisherigen Gespräch), gib sie UNVERÄNDERT zurück — füge keine "
    "Themen aus der Konversation hinzu.\n"
    "• Antworte in der Sprache der aktuellen Frage.\n"
    "• Antworte nur mit der Frage selbst — keine Erklärung, keine "
    "Anführungszeichen, kein Präfix wie 'Umformulierte Frage:'."
)

# Number of recent user/assistant pairs to feed the rewriter. Two is plenty
# for pronoun resolution and short enough that the rewriter doesn't try to
# stitch unrelated older topics into the rewrite.
_REWRITE_HISTORY_TURNS = 2
# Cap on rewriter output. Rewrites should be one short sentence; if the
# model goes long we treat it as nonsense and fall back to the original.
_REWRITE_MAX_TOKENS = 200
_REWRITE_MAX_OUTPUT_CHARS = 600

# Strip the LLM's deterministic "Quellen:" trailer (plus everything after)
# from any assistant message before showing it to the rewriter — prevents
# the rewriter from echoing cited filenames into the rewrite, which would
# bias retrieval toward already-cited documents.
_QUELLEN_TRAILER_FOR_REWRITE_RE = re.compile(
    r"\n[ \t]*(?:[*_#]+[ \t]*)?Quellen:.*", re.DOTALL,
)


def _strip_quellen_for_rewrite(content: str) -> str:
    m = _QUELLEN_TRAILER_FOR_REWRITE_RE.search(content)
    return content[: m.start()].rstrip() if m else content


def _trim_history_for_rewrite(
    history: Optional[List[Dict[str, str]]],
    turns: int = _REWRITE_HISTORY_TURNS,
) -> List[Dict[str, str]]:
    """Return up to ``turns`` user+assistant pairs from the tail, cleaned.

    Oldest-first, Quellen-trailer stripped so the rewriter sees prose.
    """
    if not history:
        return []
    tail = history[-(turns * 2):]
    out: List[Dict[str, str]] = []
    for m in tail:
        role = m.get("role") or ""
        content = m.get("content") or ""
        if role == "assistant":
            content = _strip_quellen_for_rewrite(content)
        out.append({"role": role, "content": content})
    return out


def _format_rewrite_user_prompt(
    history: List[Dict[str, str]], question: str
) -> str:
    """Render the (history, question) pair into the user-turn the rewriter
    consumes. Keeps the format mechanically simple so the rewriter LLM
    can focus on the rewrite rather than parsing."""
    lines = ["Konversation:"]
    for m in history:
        speaker = "Nutzer" if m["role"] == "user" else "Assistent"
        lines.append(f"{speaker}: {m['content']}")
    lines.append("")
    lines.append(f"Aktuelle Frage: {question}")
    return "\n".join(lines)


def _clean_rewriter_output(raw: str) -> str:
    """Trim quotes, whitespace, and obvious wrapper junk from rewriter
    output. Empty result signals to the caller to use the original."""
    if not raw:
        return ""
    s = raw.strip()
    # Strip enclosing quotes if present on both ends (common LLM tic)
    for opener, closer in (('"', '"'), ("'", "'"), ("«", "»"), ("„", "“")):
        if s.startswith(opener) and s.endswith(closer) and len(s) >= 2:
            s = s[len(opener):-len(closer)].strip()
            break
    # Strip a leading "Aktuelle Frage:" / "Umformulierte Frage:" if the
    # model echoed our label.
    for prefix in (
        "Aktuelle Frage:", "Umformulierte Frage:", "Standalone:",
        "Frage:", "Rewrite:",
    ):
        if s.lower().startswith(prefix.lower()):
            s = s[len(prefix):].strip()
            break
    return s


RerankFn = Callable[..., List[float]]


class RetrievalService:
    def __init__(
        self,
        ollama: Optional[OllamaClient] = None,
        store: Optional[QdrantStore] = None,
        session_factory: Optional[SessionFactory] = None,
        rerank_fn: Optional[RerankFn] = None,
    ) -> None:
        self.ollama = ollama or OllamaClient()
        self.store = store or QdrantStore()
        # session_factory is only needed when callers don't pass explicit
        # ``rerank_override`` — production wires the SQLite scope, tests
        # inject a stub or pass settings directly.
        self.session_factory: SessionFactory = session_factory or session_scope
        # rerank_fn defaults to the real bge-reranker singleton; tests
        # inject a no-network stub so the import chain stays light.
        self._rerank_fn: Optional[RerankFn] = rerank_fn

    def _load_rerank_fn(self) -> RerankFn:
        if self._rerank_fn is not None:
            return self._rerank_fn
        # Local import: the reranker module pulls in FlagEmbedding (heavy
        # transitively brings torch). Deferring lets retrieval still work
        # when the dependency isn't installed *and* reranking is disabled.
        from . import reranker
        self._rerank_fn = reranker.rerank
        return self._rerank_fn

    def _resolve_rerank(
        self, *, tenant: str, project: str
    ) -> EffectiveRerankSettings:
        with self.session_factory() as db:
            return rerank_settings_module.resolve(db, tenant=tenant, project=project)

    async def _rewrite_if_followup(
        self,
        question: str,
        history: Optional[List[Dict[str, str]]],
    ) -> str:
        """Return either the original question or a self-contained rewrite.

        Skips wholesale when ENABLE_QUERY_REWRITE is False or history is
        empty (fresh conversation has nothing to resolve against). Any
        Ollama hiccup falls back to the original — a misbehaving rewriter
        must never break /chat.
        """
        if not settings.ENABLE_QUERY_REWRITE:
            return question
        trimmed = _trim_history_for_rewrite(history)
        if not trimmed:
            return question
        model = (settings.REWRITE_MODEL or "").strip() or None  # None = CHAT_MODEL
        messages = [
            {"role": "system", "content": REWRITE_SYSTEM_PROMPT},
            {"role": "user", "content": _format_rewrite_user_prompt(trimmed, question)},
        ]
        try:
            raw = await self.ollama.chat(
                messages,
                model=model,
                temperature=0.0,
                max_tokens=_REWRITE_MAX_TOKENS,
            )
        except OllamaError as exc:
            log.warning("Query rewrite failed (%s); using original question", exc)
            return question
        cleaned = _clean_rewriter_output(raw)
        if not cleaned or len(cleaned) > _REWRITE_MAX_OUTPUT_CHARS:
            log.warning(
                "Rewriter produced unusable output (len=%d); using original",
                len(cleaned),
            )
            return question
        if cleaned != question:
            log.info("Query rewrite: %r -> %r", question, cleaned)
        return cleaned

    async def retrieve(
        self,
        *,
        tenant: str,
        project: str,
        question: str,
        top_k: Optional[int] = None,
        min_score: Optional[float] = None,
        rerank_override: Optional[EffectiveRerankSettings] = None,
        past_citation_ids: Optional[List[str]] = None,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> List[SearchHit]:
        """Run the retrieval pipeline for a single query.

        ``history`` (optional) is the recent user/assistant pair list used
        by the query-rewriter to resolve pronoun / reference follow-ups
        before the embed call. ``past_citation_ids`` is the orthogonal
        candidate-pool injection of chunks cited in recent turns.
        """
        k = top_k or settings.RETRIEVAL_TOP_K
        threshold = min_score if min_score is not None else settings.MIN_RETRIEVAL_SCORE

        rerank_cfg = rerank_override or await asyncio.to_thread(
            self._resolve_rerank, tenant=tenant, project=project
        )

        # Overfetch covers two needs: rerank candidates AND stem-dedup
        # slack. We always pull at least 2*k so the dedup step downstream
        # can drop DOCX/PDF duplicates without starving the final top-K.
        qdrant_k = max(k * 2, rerank_cfg.overfetch_k) if rerank_cfg.enabled else k * 2

        # Rewrite first, so embedding + reranker + synonyms all see the
        # self-contained form of the question.
        effective_question = await self._rewrite_if_followup(question, history)
        expanded = _expand_query_with_synonyms(effective_question)
        vec = await self.ollama.embed(expanded)

        hits = await asyncio.to_thread(
            self.store.search,
            tenant=tenant,
            project=project,
            query_vector=vec,
            top_k=qdrant_k,
            score_threshold=threshold,
        )

        # Fold in recently-cited chunks if any. Done before reranking so the
        # cross-encoder can compare apples to apples: every candidate gets
        # a fresh relevance score for the *new* question.
        if past_citation_ids:
            past_hits = await asyncio.to_thread(
                self.store.get_points_by_ids,
                tenant=tenant,
                project=project,
                point_ids=past_citation_ids,
            )
            hits = _merge_unique_by_point_id(hits, past_hits)

        if not rerank_cfg.enabled or len(hits) <= 1:
            # Reranking a single hit is a no-op; reranking zero is undefined.
            return _dedup_by_stem(hits)[:k]

        passages = [str((h.payload or {}).get("text") or "") for h in hits]
        try:
            scores = await asyncio.to_thread(
                self._load_rerank_fn(),
                query=expanded,
                passages=passages,
                model_name=rerank_cfg.model,
            )
        except Exception as exc:
            # Don't fail the user's query because the reranker hiccupped —
            # log loud, fall back to bi-encoder order. The /health probe
            # will surface the underlying issue separately.
            log.warning(
                "Reranker failed (%s); falling back to bi-encoder order for "
                "tenant=%s project=%s",
                exc, tenant, project,
            )
            return _dedup_by_stem(hits)[:k]

        # Replace the bi-encoder score with the reranker score so downstream
        # display and downstream gating stay consistent with the new order.
        rescored: List[SearchHit] = []
        for h, s in zip(hits, scores):
            rescored.append(SearchHit(score=float(s), payload=h.payload, point_id=h.point_id))
        rescored.sort(key=lambda h: h.score, reverse=True)
        return _dedup_by_stem(rescored)[:k]
