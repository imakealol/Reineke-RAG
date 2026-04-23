# Architecture Decision Records

Each ADR captures **one** significant decision, its context, alternatives, and consequences. Format loosely follows Michael Nygard's template.

| # | Title | Status |
|---|-------|--------|
| [001](ADR-001-document-parser.md) | Docling for document parsing | Accepted |
| [002](ADR-002-vector-db.md) | Qdrant for vector + sparse retrieval | Accepted |
| [003](ADR-003-embeddings.md) | bge-m3 as the single embedding model | Accepted |
| [004](ADR-004-llm-stack.md) | Tiered Ollama LLM stack with a router | Accepted (rev 2026-04-23: Mac heavy-tier = 32B; 70B opt-in) |
| [005](ADR-005-auth.md) | Authentik as the identity provider | Accepted |
| [006](ADR-006-xlsx-handling.md) | DuckDB SQL path alongside vector path for XLSX | Accepted |
| [007](ADR-007-ui.md) | Open WebUI with custom pipeline | Accepted |
| [008](ADR-008-framework-vs-services.md) | Thin FastAPI services over LangChain/LlamaIndex as framework | Accepted |
| [009](ADR-009-single-document-queries.md) | Single-document queries as a first-class path | Accepted |
| [010](ADR-010-ollama-on-apple-silicon.md) | Ollama runs natively on Apple Silicon; not in Docker | Accepted |

Revisions: if a decision needs to change, write a new ADR superseding the old; never rewrite accepted ADRs in place.
