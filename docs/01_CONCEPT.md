# 01 — Concept

> "The best RAG system is the one whose answers you would be willing to forward to a customer without reading them first." — design principle for Reineke-RAG.

---

## 1. Problem statement

The existing n8n "self-hosted AI starter kit" is a thin demo built around four assumptions that break in a real corpus of internal Office documents:

1. **A PDF is a stream of text.** It is not. Real PDFs contain multi-column layouts, rotated pages, tables, footnotes, embedded images, and scanned regions. Treating them as raw text corrupts every downstream step.
2. **Fixed-size chunking is good enough.** It isn't. Fixed windows split mid-table, separate a heading from its paragraph, and split a row from its column header — destroying the structural signals a retriever needs.
3. **Dense-vector retrieval alone suffices.** It doesn't. Dense retrieval misses exact terms: part numbers (`KR-4711-B`), product names, German compounds (`Einbauvorschrift`), and legal clauses. Real queries mix semantics with keywords.
4. **One LLM + one embedder solves every query.** It doesn't. A question about a price in an Excel file needs a *SQL-style* path. A cross-document synthesis question needs reranking and wider context. A short factual lookup needs speed.

The result is a stack that demos beautifully on a few PDFs and falls apart at 500+ real business files — precisely the regime where it becomes interesting.

## 2. Requirements (locked in with the owner)

| # | Requirement | Source |
|---|-------------|--------|
| R1 | Supports **PDF, DOCX, XLSX** reliably, incl. scanned PDFs (OCR) and complex tables. | Owner |
| R2 | Documents are a **mix of German and English**. Retrieval and generation must handle both equally. | Owner |
| R3 | Corpus of **500 – 10 000 documents, growing**. Incremental ingestion, deduplication, re-indexing. | Owner |
| R4 | **Company-wide usage with role-based access** per folder. Audit log mandatory. | Owner |
| R5 | Four answer styles must work: **Q&A with citations**, **extraction/summarisation**, **table/spreadsheet reasoning**, **cross-document synthesis**. | Owner |
| R6 | **Fully offline** — no SaaS calls, no telemetry leaving the LAN. | Owner |
| R7 | Runs on **Apple M4 Max, 64 GB RAM** as the reference dev box; ports cleanly to Linux/x86 + GPU. | Hardware |
| R8 | Must be **deployable and operable** by a small IT team via Docker Compose. | Implicit |

## 3. Design principles

Everything in the stack is chosen against these principles, in order:

1. **Correctness over speed.** A wrong answer with a citation is worse than a slow answer with a citation.
2. **Every answer cites.** No citation → no answer. The LLM is prompted to refuse when no context supports a claim.
3. **Structure is information.** Tables, headings, page breaks, sheet boundaries are preserved from ingestion through retrieval.
4. **Hybrid everything.** Dense + sparse retrieval; vector + SQL path for tables; rerank before generation.
5. **Offline by default.** Every external call is a design smell to be eliminated.
6. **Boring tech where it counts.** PostgreSQL, Docker, standard OIDC — not bleeding-edge stores unless justified.
7. **Single source of truth per concern.** Postgres = metadata + ACLs + audit. Qdrant = vectors. MinIO = bytes. DuckDB = tabular data. No duplication.
8. **Observability from day one.** Every retrieval, every generation, every rerank is logged to Langfuse with latency + cost (token) breakdown.

## 4. Architecture at a glance

```
                         ┌──────────────────────────────────────┐
                         │            Open WebUI                │
                         │   (chat, doc browser, citations)     │
                         └───────────────┬──────────────────────┘
                                         │   OIDC
                                         ▼
                         ┌──────────────────────────────────────┐
                         │   Authentik  (SSO / groups / ACL)    │
                         └───────────────┬──────────────────────┘
                                         │ JWT
                      ┌──────────────────┴────────────────────┐
                      ▼                                       ▼
       ┌────────────────────────┐               ┌──────────────────────────┐
       │  Retrieval API         │               │  Ingestion API           │
       │  (FastAPI)             │               │  (FastAPI + RQ worker)   │
       │                        │               │                          │
       │  1. query rewrite      │               │  1. upload → MinIO       │
       │  2. dense + sparse     │               │  2. Docling parse        │
       │  3. rerank             │               │  3. structure-aware      │
       │  4. SQL path (XLSX)    │               │     chunking             │
       │  5. cite + generate    │               │  4. embed (bge-m3)       │
       │                        │               │  5. upsert Qdrant        │
       │                        │               │  6. tables → DuckDB      │
       └──┬──────┬─────────┬───┘               └──┬───────────┬───────────┘
          │      │         │                      │           │
          ▼      ▼         ▼                      ▼           ▼
       Ollama  Qdrant   Reranker              Docling     Qdrant+DuckDB
      (LLMs   (vectors  (bge-re-v2-m3        (service)   (+MinIO+Postgres)
      +emb)    +BM25)    via TEI/Infinity)
          │
          └──────────────── Langfuse (observability)
                            Prometheus + Grafana (infra)

   Postgres  ←── metadata, users' groups, doc ACLs, audit log, job state
   Redis     ←── ingestion job queue
   MinIO     ←── original files (versioned, immutable)
   DuckDB    ←── structured tables extracted from XLSX
   Caddy     ←── reverse proxy, HTTPS, routing
```

Each box is one container in a single `docker-compose.yml` with Compose *profiles* so optional pieces (n8n, Langfuse) can be disabled.

## 5. Technology stack — the short list

| Layer | Choice | Why (short) |
|-------|--------|-------------|
| Document parsing | **Docling** (IBM, Apache 2.0) | Best-in-class for tables, formulas, multi-column PDF, DOCX, XLSX, PPTX; built-in HybridChunker respects document structure. |
| OCR (embedded in Docling) | **EasyOCR** or **Tesseract** | For scanned PDFs. Swappable. |
| Embeddings (dense + sparse) | **bge-m3** via Ollama | Multilingual (100+ langs, excellent DE/EN), 8 k context, native dense + sparse output, single model for both. |
| Vector DB | **Qdrant** | Native hybrid search (dense + sparse) since 1.10, ACL-ready payload filtering, fast Rust core, strong auth. |
| Reranker | **bge-reranker-v2-m3** via TEI (HF Text Embeddings Inference) | Multilingual cross-encoder, small, fast on Apple Silicon with Metal. |
| LLM server | **Ollama** | Apple-Silicon-native, Metal accel, one binary, huge model catalog, OpenAI-compatible API. |
| LLMs (see ADR-004) | **Qwen 2.5 32B-Instruct** (reasoning), **Llama 3.3 70B** (heavyweight), **Gemma 2 9B** (fast) | All strong multilingual; three tiers to route by query type. |
| XLSX table store | **DuckDB** | Embedded, file-based, blazing SQL on local data, zero ops. |
| Object storage | **MinIO** | S3-compatible, single-container, versioned files. |
| Metadata store | **PostgreSQL 16** | Users, groups, docs, chunks registry, ACLs, audit log, job state. |
| Queue | **Redis** + **RQ** | Simple job queue for ingestion; Redis also used by Open WebUI. |
| Identity | **Authentik** | Modern OIDC IdP, easy group management, SSO for all services. |
| UI | **Open WebUI** | Mature Ollama-native chat UI, OIDC ready, pipelines feature to call our retrieval API. |
| Orchestration (optional) | **n8n** | Scheduled jobs (nightly reindex, folder watcher, report generators). |
| Observability (LLM) | **Langfuse (self-hosted)** | Traces every RAG call: retrieval, rerank, prompts, tokens, latency. |
| Observability (infra) | **Prometheus + Grafana + Loki** | Container metrics + logs. |
| Reverse proxy | **Caddy** | Automatic HTTPS via local CA, simple config. |

One-line rejection for the most likely alternatives (full discussion in ADRs):

- **Unstructured.io** → less accurate on tables than Docling, commercial pressure on the OSS edition.
- **PyMuPDF-only pipeline** → fast but loses layout and tables.
- **pgvector** → fine up to ~1 M vectors but no hybrid search without extensions; Qdrant is simpler and more capable.
- **LlamaIndex / Haystack as the main framework** → we use their *components* (chunkers, loaders) but not the framework itself; a thin FastAPI gives better control and observability.
- **Keycloak instead of Authentik** → Keycloak is fine but heavier; Authentik ships better defaults for this scale.

## 6. Data flows

### 6.1 Ingestion

1. **Drop** a file into a watched folder on the NAS/local disk (or POST to the upload API).
2. **Uploader** records metadata in Postgres (`documents` table), stores the immutable original in MinIO under `raw/{doc_id}/{filename}`, queues an ingestion job in Redis.
3. **Worker** pulls the job, runs Docling → produces a `DoclingDocument` (structured, typed).
4. **Chunker** (Docling HybridChunker) produces chunks that respect headings, tables, list items, page boundaries. Each chunk carries: `doc_id`, `chunk_id`, `page`, `section_path`, `content_type` (`text` / `table` / `list` / `formula`), `hash`, `language`, `acl_tags`.
5. **Embedder** calls Ollama `/api/embeddings` with the bge-m3 model to get dense vectors; sparse vectors computed locally with FastEmbed/bge-m3 sparse model.
6. **Upsert** into Qdrant collection `chunks` (payload includes all metadata above).
7. **Tables** extracted from XLSX (and PDF tables where confident) additionally land as columnar data in DuckDB under `tables.{doc_id}_{sheet}`.
8. Job state transitions: `queued → parsing → embedding → indexed` (or `failed` with retryable flag). Progress visible in UI.

### 6.2 Retrieval + generation

1. User asks a question in Open WebUI.
2. Open WebUI **pipeline** forwards the query to the Retrieval API with the user's JWT.
3. Retrieval API:
   1. Extracts **user's groups** from JWT → builds the Qdrant ACL filter.
   2. **Classifies** the query (small LLM call, cheap model): is it `lookup` / `extraction` / `table-math` / `synthesis`? Table-math queries go to the SQL path in parallel.
   3. **Rewrites** the query into 2–3 paraphrases (HyDE or simple) for recall.
   4. Embeds the query (dense + sparse).
   5. Qdrant **hybrid search** with ACL filter → top 50 chunks.
   6. **Reranks** via bge-reranker-v2-m3 → top 8-12.
   7. Table-math branch: LLM generates DuckDB SQL against the relevant `tables.*`, executes it, captures rows as a "citation" too.
   8. Builds a prompt with system instructions requiring citations, plus the reranked chunks + (optional) SQL result table.
   9. Chooses an LLM tier by query class (see ADR-004), streams the answer back.
4. Open WebUI renders answer + clickable citations (each citation links back to the original file in MinIO with page highlight).
5. Langfuse receives the full trace.

### 6.3 Query classes → routing

| Class | Example | Path | LLM tier |
|-------|---------|------|----------|
| Lookup | "Welche Norm gilt für Schraubverbindungen in Typ-B-Schränken?" | Hybrid + rerank | Gemma 2 9B |
| Extraction | "Liste alle Lieferfristen aus Angebot 2024-09.pdf." | Hybrid + rerank, long context | Qwen 2.5 32B |
| Table-math | "Welches Projekt hatte 2024 die höchste Marge?" | SQL over DuckDB + rerank for context | Qwen 2.5 32B |
| Synthesis | "Fasse unsere Position zu Thema X über alle QMS-Dokumente zusammen." | Hybrid + rerank top 20, map-reduce | Llama 3.3 70B |

## 7. Access control model

- **Identity**: Authentik. Users belong to **groups** (`sales`, `engineering`, `qms`, …). Groups are the unit of authorisation.
- **Document taxonomy**: every document lives under a logical **folder path** (`/qms/`, `/sales/kunden/`, `/technik/konstruktion/`). Folders have a list of permitted groups.
- **Propagation**: ingestion reads the folder's ACL at ingest time and writes `acl_groups: [<group>, …]` onto every chunk's payload and into the Postgres `documents` row.
- **Enforcement**: every Qdrant query has a mandatory filter `payload.acl_groups ANY user.groups`. DuckDB queries are run through a view that applies the same filter.
- **Audit**: every query (user, query text, doc ids returned, LLM used, answer hash) is written to Postgres `audit_log`.
- **Re-evaluation**: when folder ACLs change, a reindex job rewrites the `acl_groups` payload (no re-embedding needed — payload update is cheap in Qdrant).

## 8. Model choices on M4 Max / 64 GB (reference)

| Model | Size / quant | RAM (approx.) | Role |
|-------|--------------|---------------|------|
| `gemma2:9b-instruct-q5_K_M` | ~7.5 GB | Fast path, query classifier, short Q&A |
| `qwen2.5:32b-instruct-q4_K_M` | ~20 GB | Primary reasoning, extraction, table answers |
| `llama3.3:70b-instruct-q4_K_M` | ~40 GB | Heavy synthesis; offload to RAM on M4 Max — slow but fits |
| `bge-m3` | ~1 GB | Dense + sparse embeddings |
| `bge-reranker-v2-m3` | ~600 MB | Reranker via TEI |

Only one heavyweight LLM stays hot at a time; Ollama lazy-loads and evicts. The router routes by class, so the 70B is only invoked for cross-doc synthesis (minutes of usage per day, not hours).

For Linux/x86 + GPU deployments (24 GB+ GPU), substitute `qwen2.5:72b-instruct` as the primary. ADR-004 has the full sizing matrix.

## 9. Success criteria

The build is "done" when all of these pass:

1. **Ingestion**: 100 real sample documents (PDF + DOCX + XLSX, German + English, incl. 2 scanned PDFs) processed in < 20 min on the reference box, 0 failures, all chunks have non-null section_path.
2. **Retrieval precision**: on a hand-graded 50-query eval set (built in Phase 8), top-3 contains the gold chunk ≥ 85 % of the time, top-10 ≥ 95 %.
3. **Citation fidelity**: a random audit of 30 answers shows every claim is supported by the cited chunk (zero hallucinated citations).
4. **Table reasoning**: 10 hand-written questions over 5 real XLSX files answered correctly (numeric accuracy, not just relevance).
5. **ACL**: a second test user without access to `/qms/` receives zero chunks from that folder over 20 varied queries. Audit log entries exist.
6. **Latency**: simple lookup ≤ 5 s p95 on reference box; synthesis with 70B ≤ 60 s p95.
7. **Offline**: `docker compose up` on an air-gapped machine succeeds after initial model pull; no outbound traffic during operation (verified with pfSense / Little Snitch).
8. **Operability**: a fresh admin following `04_OPERATIONS.md` can install from scratch, restore from a backup, and add a new user in < 30 minutes without additional help.

## 10. What is explicitly out of scope (v1)

- Multi-language beyond German + English (bge-m3 supports it, but we don't evaluate it).
- Real-time collaborative editing, doc generation, agentic workflows beyond n8n's built-in nodes.
- Non-file data sources (email, Slack, wiki crawl). Planned hooks exist but not delivered.
- Mobile apps. Web UI is responsive but mobile-last.
- High availability / clustering. Single-node with backups. (Mentioned in ADR for later.)

## 11. Glossary (short)

- **Chunk**: a retrieval-unit-sized slice of a document. Here: structure-aware, 200–1000 tokens.
- **Dense retrieval**: nearest-neighbour over semantic embeddings.
- **Sparse retrieval**: BM25-like term matching, provided natively by Qdrant 1.10+ and by bge-m3's sparse output.
- **Hybrid retrieval**: fusion of dense + sparse (Qdrant uses reciprocal rank fusion by default).
- **Reranking**: a second-pass cross-encoder that rescores a small candidate set for final ordering.
- **ACL**: Access Control List — here, the set of groups allowed to see a document.
- **OIDC**: OpenID Connect, the SSO protocol used between Authentik and every other service.

## 12. Open questions (for the owner, before build kickoff)

These do not block concept acceptance but shape defaults:

1. **Folder layout**: is there already a canonical folder tree? If yes, share it so we seed the ACL mapping.
2. **Group list**: initial list of Authentik groups? (Best guess: `admin, engineering, qms, sales, finance, hr, guest`.)
3. **Backup target**: NAS path or external drive? Encryption at rest desired?
4. **HTTPS certificates**: internal CA, Let's Encrypt via DNS-01, or self-signed with distribution?
5. **Preferred reranker location**: Ollama (if/when supported) vs dedicated TEI container — default TEI unless there's a pref.
6. **n8n**: do we want it active from day 1 or add later?

These are tracked in `05_IMPLEMENTATION_PLAN.md` under "Phase 0 prerequisites".
