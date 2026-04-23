# ADR-008 — Thin FastAPI services over a RAG framework

- **Status:** Accepted
- **Date:** 2026-04-23

## Context

We could build this stack as a thin skin over **LangChain**, **LlamaIndex**, or **Haystack**. Each provides loaders, chunkers, retrievers, agents. The temptation is strong: lots of off-the-shelf code.

## Decision

We **use these libraries as components** (e.g., we may import a LlamaIndex SQL generator, or a LangChain text splitter if HybridChunker isn't suitable for a specific file type) but we **do not adopt any of them as the overall framework**. Our code paths are a small set of FastAPI services with explicit, readable control flow:

- `services/ingestion-api`, `services/ingestion-worker`, `services/retrieval-api`, `services/duckdb-api`, `services/docling`.

Every request path in `retrieval-api` is ≤ 200 lines and reads top-to-bottom.

## Rationale

1. **Observability** — Langfuse tracing is easier to wire precisely when we call Ollama / Qdrant / TEI ourselves. Frameworks introduce abstraction layers that obscure latency and token usage.
2. **Security posture** — we control exactly what goes where, including ACL filters, JWT forwarding, and the DuckDB statement validator. Frameworks often bury such concerns.
3. **Upgrade velocity** — framework API churn is high (LangChain notably). Pinning everything to one framework's opinion is a liability.
4. **Reading code in 2 years** — explicit beats clever. A junior admin reading `retrieval-api/app.py` should be able to trace a query end-to-end in one file.

## What we DO import

- LangChain/LlamaIndex text splitters as *fallbacks* behind a flag (not defaults).
- LangChain's `SQLDatabase` helpers reference implementation — rewritten locally to use our DuckDB HTTP layer.
- FastAPI, Pydantic, httpx, Redis, RQ — library-grade, not framework-grade.

## Consequences

Positive:

- Lean dependency tree, fast builds, reviewable diffs.
- No framework upgrades forced on us.

Negative:

- A little more first-time code to write (~1 500 lines across the retrieval + ingestion services).
- We have to maintain our own prompt templates (not hiding behind a framework's defaults) — considered a feature for quality work.

## Consequence for agents building this

This ADR is binding: a subagent proposing to "just use LangChain RetrievalQA" fails review. Components yes, frameworks no.
