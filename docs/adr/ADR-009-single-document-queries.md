# ADR-009 — Single-document queries as a first-class path

- **Status:** Accepted
- **Date:** 2026-04-23
- **Supersedes:** none

## Context

Audit of the original retrieval pipeline (`02_ARCHITECTURE.md` §7.3, first revision) revealed that **single-document questions** — the single most common query type in real-world business use — were handled only implicitly, by the generic multi-doc path. Problems observed (by design review; the system is not yet built):

1. **Filename mentions are not a retrieval signal.** *"Welche Lieferfristen stehen in Angebot-2024-09.pdf?"* embeds the filename as ordinary tokens; chunks from other documents that mention "Lieferfrist" can out-score chunks from the actual file.
2. **Fixed `TOP_K_RERANK=12` silently truncates extraction.** *"List all prices in Projekte2024.xlsx"* needs every row, not the top 12 semantic matches.
3. **No full-document context option.** For short-to-medium documents (≤ ~20 k tokens) and summary/extraction questions, stuffing the whole document into the LLM beats any retrieval-based approach. The original design never considered it.
4. **No page- or section-anchored retrieval.** *"Was steht auf Seite 3 von X?"* has an exact answer reachable via payload filter, not semantic search.
5. **No UI affordance to scope a chat to a document.** Users can't say *"for this conversation, only look at X.pdf"*.

## Decision

Treat **query scope** (single-doc vs multi-doc) as a first-class dimension of query classification, orthogonal to the intent (lookup / extraction / summarize / table-math / synthesis). The retrieval pipeline gets a new **scope extraction** step before classification and five distinct execution paths.

### 1. Scope extraction (new, deterministic, runs before LLM classification)

Before any model call, the retrieval API runs a fast rule-based extractor on the query + the most recent few turns of the conversation:

- **Filename tokens**: `\b[\w\-._]+\.(pdf|docx|xlsx|doc|xls|pptx)\b` → lookup in Postgres `rag.documents` by `filename`. Fuzzy fallback (`pg_trgm` similarity ≥ 0.6) for near-matches like "Angebot September" → `Angebot-2024-09.pdf`.
- **Explicit doc IDs**: previously cited documents in the conversation carry forward. If the user's current turn is a follow-up (short, no new file mention), inherit the doc_id set from the previous turn.
- **UI scope**: the chat UI exposes a **"scope chip"** that the user can attach to a chat: *All documents* (default), *This folder: /…*, or *This document: X.pdf*. The pipeline propagates the chip as structured fields, not free text.
- **Page / section tokens**: `Seite (\d+)` / `page (\d+)` / `§ ?([\d.]+)` → stored as `scope.page_filter`, `scope.section_prefix`.

Output of this step:

```python
Scope(
    doc_ids: list[UUID] | None,         # None = whole corpus (ACL still applies)
    folder_paths: list[str] | None,
    page_filter: int | None,
    section_prefix: str | None,
    confidence: float,                  # 0..1; < 0.5 = treat as multi-doc
)
```

Scope extraction is **deterministic, cheap, and testable**. LLM-free. If ambiguous (confidence < 0.5), scope falls back to corpus-wide and a small UI hint is emitted: *"Searching all documents — to scope to a file, click [Add scope]."*

### 2. Updated query classifier

The classifier (fast LLM, one prompt) now outputs two orthogonal labels:

- `scope_label`: `single-doc` | `single-folder` | `multi-doc`
- `intent`: `lookup` | `extraction` | `summarize` | `table-math` | `synthesis`

The classifier is **advisory**; the deterministic scope extractor's output overrides `scope_label` when `confidence ≥ 0.5`. This keeps the system honest when the user explicitly names a file.

### 3. Five execution paths

| Scope | Intent | Execution |
|-------|--------|-----------|
| `single-doc` | `lookup` | Hybrid search with **`doc_id` filter**, rerank top 30 → top 8. LLM: Fast tier. |
| `single-doc` | `extraction` | **Fetch ALL chunks** of the doc, ordered by `ord`. If total ≤ 20 k tokens: pass wholesale. If larger: map-reduce per section, aggregated. No rerank (we want completeness, not ranking). LLM: Reasoning tier. |
| `single-doc` | `summarize` | Same as extraction, but prompt asks for a structured summary. If doc ≤ 20 k tokens: single-pass summary. Larger: map-reduce per section + final synthesis. LLM: Reasoning tier. |
| `single-doc` | `table-math` | DuckDB SQL path **filtered to this doc's tables only**; context passage drawn from the doc's chunks. LLM: Reasoning tier. |
| `multi-doc` | `*` | Existing hybrid + rerank pipeline (unchanged except for cleaner boundaries). |
| `single-folder` | `*` | Hybrid + rerank with `folder_path` prefix filter. Same otherwise. |

Page-anchored retrieval (`scope.page_filter` set) always adds `payload.page = N` to the Qdrant filter; section-anchored adds `payload.section_path LIKE "<prefix>%"`.

### 4. Full-document context mode

A single-doc intent of `summarize` or `extraction`, on a document where `sum(chunk.token_count) ≤ FULL_DOC_CONTEXT_THRESHOLD` (default **20 480 tokens**, tunable), skips retrieval entirely and passes the concatenated chunk text to the LLM with section-path markers preserved. This beats retrieval on small docs by construction — no information is hidden behind a rank cutoff. For docs over threshold, map-reduce is used; the threshold is deliberately low enough to always fit comfortably in a 32 k-context model with headroom for the answer.

### 5. UI affordance (v1)

Open WebUI's pipeline renders a **scope chip** below the input field:
- Default: *Alle Dokumente / All documents*.
- Click opens a searchable list of documents the user can read; selecting one scopes subsequent turns in this chat.
- Citations clicked in any previous answer offer *"Auf dieses Dokument beschränken"* as a one-click action.

Behind the scenes: the chip is serialised as a `x-reineke-scope` header from the pipeline to the retrieval API. The retrieval API trusts the chip for scope only; **ACL is still enforced independently** — a chip cannot grant access.

## Alternatives considered

- **LLM-only scope detection.** Letting the classifier decide whether a filename is a scope hint produces false negatives on partial matches (e.g. "Angebot September") and adds a model call to the hot path. Deterministic extraction first + LLM classification for intent is faster and more reliable.
- **Always do full-doc context** for small docs regardless of intent. Too expensive when the user only wanted one field; increases token bill on Ollama. Intent-gating keeps lookups cheap.
- **Separate endpoint `POST /document/{id}/query`.** Cleaner contract, but forces the UI to decide scope up front. The chip approach gives users a gradual slope from "ask broadly" to "ask narrowly".

## Consequences

Positive:

- *"Welche Lieferfristen stehen in X.pdf?"* becomes a filter query, not a gamble. Accuracy approaches 100 % on well-parsed docs.
- *"Fasse X.pdf zusammen"* on a 10-page doc returns a faithful summary — the model saw the whole thing.
- *"List all prices in Y.xlsx"* goes through the DuckDB path with a `doc_id` predicate: complete, cited, verifiable.
- Scope chip gives users agency without requiring them to learn prompting tricks.

Negative:

- More code paths in the retrieval service (five vs one). Mitigated by a strict single-entry dispatcher (`route(scope, intent) -> Handler`) with one handler per cell; the control flow stays readable.
- The "inherit doc_id from conversation" heuristic can be wrong. Mitigation: the UI always shows the active scope in the chip; the user can clear it with one click.
- Full-doc context for long-ish DOCX (e.g. 18 k tokens) uses a lot of LLM tokens per question. Budget guardrail: a single-doc chat truncates the active scope to a user-visible warning when doc size crosses a second threshold (default 30 k tokens) and falls back to retrieval-plus-full-section context.

## New acceptance criteria (added to Phase 6 + 8)

- **A6.6** — Filename-anchored query: *"Liste alle Lieferfristen aus {file}.pdf"* returns a list that matches the hand-extracted ground truth with ≥ 95 % recall.
- **A6.7** — Full-doc summary: on a ≤ 15 k-token DOCX, the generated summary contains all five hand-picked gold points; verified by a separate LLM-as-judge pass against the ground truth.
- **A6.8** — Scope chip end-to-end: attaching the chip causes all subsequent Qdrant calls in that chat to include `doc_id` filter; a corpus-wide chunk never appears in citations.
- **A6.9** — Page-anchored query: *"Was steht auf Seite 3 von {file}.pdf?"* returns only chunks with `page = 3` and correct doc_id.
- **A8 Eval partitions** — the gold set of **≥ 100 queries** is split 40 / 30 / 20 / 10 into single-doc / multi-doc-lookup / table-math / synthesis; each bucket has its own recall / faithfulness target.

## Implementation ownership

`retrieval-agent` implements scope extraction, updated classifier, and the five handlers. `ui-agent` implements the scope chip in the Open WebUI pipeline. No changes to ingestion — the existing payload already carries `doc_id`, `page`, `section_path`.
