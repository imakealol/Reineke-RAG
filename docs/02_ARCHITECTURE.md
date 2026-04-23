# 02 — Architecture

> This document is the technical reference for Reineke-RAG. It covers every component, every interface, every schema and every operational contract. The concept ([01_CONCEPT.md](01_CONCEPT.md)) explains *why*; this one explains *how*.

---

## 1. Component inventory

| # | Name | Container / image | Role | Exposes |
|---|------|-------------------|------|---------|
| 1 | **caddy** | `caddy:2-alpine` | Reverse proxy, HTTPS (internal CA), routing | 80, 443 (LAN) |
| 2 | **authentik-server** | `ghcr.io/goauthentik/server:latest` | OIDC IdP, user + group mgmt | internal 9000 |
| 3 | **authentik-worker** | same | Background tasks | — |
| 4 | **authentik-db** | `postgres:16-alpine` | Authentik's own DB | internal 5432 |
| 5 | **authentik-redis** | `redis:7-alpine` | Authentik cache | internal 6379 |
| 6 | **postgres** | `postgres:16-alpine` | App metadata, ACLs, audit, job state | internal 5432 |
| 7 | **redis** | `redis:7-alpine` | RQ job queue, pub/sub | internal 6379 |
| 8 | **minio** | `quay.io/minio/minio` | S3-compatible object store for raw files | internal 9000/9001 |
| 9 | **qdrant** | `qdrant/qdrant:v1.12.0` | Vector + sparse index, payload filters | internal 6333 (REST), 6334 (gRPC) |
| 10 | **ollama** | `ollama/ollama:latest` | LLM + dense embedding server | internal 11434 |
| 11 | **tei-reranker** | `ghcr.io/huggingface/text-embeddings-inference:latest` | bge-reranker-v2-m3 server | internal 8080 |
| 12 | **docling** | custom (Python + Docling) | Document parsing service | internal 8001 |
| 13 | **ingestion-api** | custom FastAPI | Upload, queue, status | internal 8010 |
| 14 | **ingestion-worker** | same image, worker mode | Pulls jobs, runs full pipeline | — |
| 15 | **retrieval-api** | custom FastAPI | Query rewrite, retrieval, rerank, generate | internal 8020 |
| 16 | **duckdb-api** | custom FastAPI | SQL executor over `tables.*` with ACL view | internal 8030 |
| 17 | **openwebui** | `ghcr.io/open-webui/open-webui:main` | End-user chat UI | via Caddy |
| 18 | **pipelines** | `ghcr.io/open-webui/pipelines:main` | Open WebUI → Retrieval API bridge | internal 9099 |
| 19 | **langfuse** | `langfuse/langfuse:2-latest` | LLM tracing & evals | via Caddy at `/langfuse` |
| 20 | **langfuse-db** | `postgres:16-alpine` | Langfuse backend | internal 5432 |
| 21 | **prometheus** | `prom/prometheus` | Metrics scrape | internal 9090 |
| 22 | **grafana** | `grafana/grafana` | Dashboards | via Caddy at `/grafana` |
| 23 | **loki** + **promtail** | `grafana/loki`, `grafana/promtail` | Log aggregation | internal |
| 24 | **n8n** (profile: `automation`) | `n8nio/n8n` | Scheduled jobs, folder watcher | via Caddy at `/n8n` |
| 25 | **watcher** (profile: `automation`) | custom | Inotify / fsevents folder watcher | — |

Total baseline (no `automation` profile): 19 containers. Heavy on component count but each is a single-purpose, well-understood box.

## 2. Network and security topology

- One Docker network: `reineke` (bridge, internal only for data-plane services).
- Only **caddy** binds host ports (80/443). Every other service talks inside the network via service name.
- **mTLS** inside the cluster is *not* used (v1). We rely on network isolation + service-to-service bearer tokens.
- **Bearer tokens / API keys** are injected via environment variables from a `.env` file (never committed). Rotated by admin via `make rotate-secrets`.
- **Authentik** is the trust root:
  - Open WebUI, Langfuse, Grafana, n8n → protected by OIDC (login via Authentik).
  - retrieval-api, ingestion-api, duckdb-api → validate JWTs signed by Authentik (RS256, JWKS fetched).
- **Qdrant** is protected by API-key header; only the APIs hold the key.
- **MinIO** is protected by an IAM-style access key pair; only the APIs hold the keys. Pre-signed URLs are issued to the UI for doc preview/download.
- **Ollama** has no auth (upstream limitation) — relies on network isolation; never bind its port publicly.

Mermaid-style sketch (logical):

```
                LAN ─┬── https://rag.company.local ──► caddy
                     │
         ┌───────────┼─────────────────────────────────────────┐
         │           │   Docker network: reineke               │
         │   ┌───────┴──────┐                                  │
         │   │  open-webui  │────── /pipelines ───────► pipelines
         │   └───┬──────────┘                                 │
         │       │ OIDC                                       │
         │   ┌───▼──────┐                                     │
         │   │authentik │                                     │
         │   └──────────┘                                     │
         │                                                    │
         │  pipelines ──► retrieval-api ──► qdrant            │
         │                    │         └─► tei-reranker      │
         │                    │         └─► ollama (LLM)      │
         │                    │         └─► duckdb-api        │
         │                    │         └─► postgres (audit)  │
         │                    └── Langfuse SDK ──► langfuse   │
         │                                                    │
         │  ingestion-api ──► redis (queue)                   │
         │  ingestion-worker ──► docling                      │
         │                   └──► ollama (embed)              │
         │                   └──► qdrant                      │
         │                   └──► duckdb-api (load tables)    │
         │                   └──► minio                       │
         │                                                    │
         └─ prometheus ◄── every /metrics endpoint            │
            loki ◄── promtail ◄── every stdout log            │
            grafana ◄── prometheus + loki                     │
```

## 3. Storage layout

All mounts live under `${DATA_ROOT}` (default `/var/lib/reineke`). One backup target per directory keeps the restore story simple.

```
${DATA_ROOT}/
├── postgres/              # main app DB
├── authentik-db/          # Authentik DB (separate instance)
├── langfuse-db/           # Langfuse DB
├── redis/                 # persistent AOF
├── minio/                 # object store (raw/, export/)
├── qdrant/                # vector storage + snapshots
├── duckdb/                # one .duckdb per collection
├── ollama/                # model weights (large: ~80 GB if all tiers pulled)
├── tei/                   # reranker model cache
├── docling/               # docling model cache
├── loki/                  # log store
└── grafana/               # dashboards + grafana state

${CONFIG_ROOT}/             # config, mounted read-only into containers
├── caddy/Caddyfile
├── authentik/              # blueprint YAMLs for initial setup
├── prometheus/prometheus.yml
├── grafana/provisioning/
├── retrieval/prompts/      # system prompt templates
└── docling/config.yaml
```

## 4. Data model (PostgreSQL)

Schema `rag` on the main `postgres` instance. Omitting Authentik / Langfuse / n8n schemas which live in their own DBs.

```sql
-- Users come from Authentik via OIDC; we mirror the minimum for audit joins.
CREATE TABLE rag.users (
  id             UUID PRIMARY KEY,        -- Authentik sub
  email          TEXT UNIQUE NOT NULL,
  display_name   TEXT,
  groups         TEXT[] NOT NULL DEFAULT '{}',
  last_seen_at   TIMESTAMPTZ,
  created_at     TIMESTAMPTZ DEFAULT now()
);

-- Folder ACLs: the unit of access control.
CREATE TABLE rag.folders (
  path           TEXT PRIMARY KEY,        -- e.g. '/qms/normen'
  acl_groups     TEXT[] NOT NULL,         -- groups that may read
  description    TEXT,
  created_at     TIMESTAMPTZ DEFAULT now(),
  updated_at     TIMESTAMPTZ DEFAULT now()
);

-- Every ingested file.
CREATE TABLE rag.documents (
  id             UUID PRIMARY KEY,
  folder_path    TEXT NOT NULL REFERENCES rag.folders(path) ON UPDATE CASCADE,
  filename       TEXT NOT NULL,
  mime_type      TEXT NOT NULL,
  size_bytes     BIGINT NOT NULL,
  sha256         CHAR(64) NOT NULL,
  language       TEXT,                    -- detected per-doc dominant language
  uploaded_by    UUID REFERENCES rag.users(id),
  uploaded_at    TIMESTAMPTZ DEFAULT now(),
  parsed_at      TIMESTAMPTZ,
  status         TEXT NOT NULL            -- queued|parsing|embedding|indexed|failed
                 CHECK (status IN ('queued','parsing','embedding','indexed','failed','superseded')),
  error          TEXT,
  pages          INT,
  minio_key      TEXT NOT NULL,           -- 'raw/{id}/{filename}'
  UNIQUE (folder_path, filename)
);

CREATE INDEX ON rag.documents (sha256);   -- dedupe
CREATE INDEX ON rag.documents (status);

-- Every chunk (for admin visibility; vectors live in Qdrant).
CREATE TABLE rag.chunks (
  id             UUID PRIMARY KEY,
  document_id    UUID NOT NULL REFERENCES rag.documents(id) ON DELETE CASCADE,
  ord            INT NOT NULL,            -- position in doc
  page           INT,                     -- 1-based
  section_path   TEXT,                    -- e.g. '2.1 Scope > Definitions'
  content_type   TEXT NOT NULL,           -- text|table|list|formula|caption
  token_count    INT,
  content        TEXT NOT NULL,           -- canonicalized text
  content_hash   CHAR(64) NOT NULL,       -- for incremental reindex
  UNIQUE (document_id, ord)
);

-- Structured tables extracted for SQL path (one row per extracted table).
CREATE TABLE rag.tables (
  id             UUID PRIMARY KEY,
  document_id    UUID NOT NULL REFERENCES rag.documents(id) ON DELETE CASCADE,
  sheet_name     TEXT,                    -- XLSX only
  duckdb_table   TEXT NOT NULL,           -- physical table name in duckdb
  columns        JSONB NOT NULL,          -- [{name, type, sample}]
  row_count      INT NOT NULL,
  page           INT                      -- PDF tables only
);

-- Every request that passed through retrieval-api.
CREATE TABLE rag.audit_log (
  id             BIGSERIAL PRIMARY KEY,
  ts             TIMESTAMPTZ DEFAULT now(),
  user_id        UUID REFERENCES rag.users(id),
  query          TEXT NOT NULL,
  query_class    TEXT,                    -- lookup|extraction|table-math|synthesis
  retrieved      JSONB,                   -- [{doc_id, chunk_id, score, reranker_score}]
  sql_executed   TEXT,                    -- for table-math queries
  llm_model      TEXT,
  llm_tokens_in  INT,
  llm_tokens_out INT,
  latency_ms     INT,
  answer_hash    CHAR(64),
  langfuse_trace TEXT
);

-- Job state mirror for the UI (RQ state is ephemeral).
CREATE TABLE rag.jobs (
  id             UUID PRIMARY KEY,
  kind           TEXT NOT NULL,           -- ingest|reindex|reacl
  document_id    UUID REFERENCES rag.documents(id) ON DELETE CASCADE,
  state          TEXT NOT NULL,           -- queued|running|done|failed
  attempts       INT DEFAULT 0,
  last_error     TEXT,
  enqueued_at    TIMESTAMPTZ DEFAULT now(),
  started_at     TIMESTAMPTZ,
  finished_at    TIMESTAMPTZ
);
```

## 5. Vector store (Qdrant)

One main collection per tenant/deployment; v1 ships a single tenant.

```jsonc
// Collection: chunks
{
  "vectors": {
    "dense": { "size": 1024, "distance": "Cosine" }      // bge-m3 dense
  },
  "sparse_vectors": {
    "sparse": { }                                         // bge-m3 sparse / BM25 fusion
  },
  "on_disk_payload": true,
  "quantization_config": {
    "scalar": { "type": "int8", "always_ram": false }
  }
}
```

Payload schema (every point):

```jsonc
{
  "doc_id": "uuid",
  "chunk_id": "uuid",
  "ord": 17,
  "page": 3,
  "section_path": "2.1 Scope > Definitions",
  "content_type": "text",
  "language": "de",
  "folder_path": "/qms/normen",
  "acl_groups": ["qms", "engineering"],
  "content_preview": "Die Norm DIN 18065 regelt…",       // for debugging UI
  "uploaded_at": "2026-04-01T10:00:00Z"
}
```

Indexed payload fields (for efficient filtering): `acl_groups`, `folder_path`, `content_type`, `language`, `doc_id`.

## 6. DuckDB table store

One DuckDB file per logical dataset: `${DATA_ROOT}/duckdb/reineke.duckdb`. Schemas:

- `raw.*` — one table per extracted XLSX sheet / PDF table, named `t_{doc_id_short}_{sheet_or_index}`.
- `meta.catalog` — `(table_name, doc_id, sheet_name, acl_groups TEXT[], columns JSON, registered_at)`.
- `views.*` — per-user ACL-filtered views materialised on demand by duckdb-api.

The duckdb-api is the **only** writer; retrieval-api reads via HTTP with a user JWT. DuckDB itself is single-writer/multi-reader, which matches this architecture.

## 7. Services — interface contracts

All custom services follow the same conventions:

- JSON over HTTP. OpenAPI spec served at `/openapi.json`.
- Auth via `Authorization: Bearer <JWT>` (Authentik-signed) *except* `/healthz` and internal service-to-service endpoints (auth by shared secret header `X-Internal-Token`).
- Structured JSON logs to stdout. Prometheus metrics at `/metrics`.

### 7.1 ingestion-api (port 8010)

```
POST  /documents                    upload (multipart) → 202 + job_id
GET   /documents?folder=/qms        list
GET   /documents/{doc_id}           metadata + status
GET   /documents/{doc_id}/download  pre-signed MinIO URL
DELETE /documents/{doc_id}          mark superseded (soft) or hard delete
POST  /documents/{doc_id}/reindex   re-run pipeline with current parser settings
GET   /jobs/{job_id}                job status
POST  /folders                      create folder + ACL
PATCH /folders/{path}               change ACL → triggers payload rewrite job
GET   /folders                      list
GET   /healthz   /metrics
```

Upload flow detail:

1. Validate user has `admin` group *or* group in folder's `acl_groups` with `write=true` flag (v1: any group member can upload to folders they can read; v2: separate read/write).
2. Stream to MinIO under `raw/{uuid}/{filename}`.
3. Compute SHA-256 while streaming → dedupe (if a doc with same sha + same folder exists, return `409` with existing doc_id).
4. Insert `rag.documents` with status `queued`, insert `rag.jobs`.
5. `rq.enqueue('ingest', doc_id)`.
6. Return `202` with `{doc_id, job_id}`.

### 7.2 ingestion-worker (no HTTP)

Pulls jobs from Redis. Pipeline:

```
ingest(doc_id):
    doc = db.get_document(doc_id); mark parsing
    bytes = minio.get(doc.minio_key)
    parsed = docling_http_post("/parse", bytes, doc.mime_type)
                 # returns DoclingDocument JSON
    chunks = hybrid_chunker.chunk(parsed,
                max_tokens=512,
                preserve_tables=True,
                preserve_sections=True)
    mark embedding
    dense = ollama.embed("bge-m3", [c.text for c in chunks])
    sparse = bge_m3_sparse([c.text for c in chunks])
    qdrant.upsert("chunks", points=[
        Point(id=c.id, vectors={"dense": d, "sparse": s}, payload=c.meta)
        for c,d,s in zip(chunks, dense, sparse)
    ])
    db.bulk_insert_chunks(chunks)
    if doc.mime_type == "xlsx":
        tables = docling.extract_tables(parsed)
        duckdb_api.register_tables(doc_id, tables)  # writes meta.catalog
    mark indexed
```

Retries: exponential backoff, max 3, then state `failed` + error stored. Admin UI shows a "retry" button.

### 7.3 retrieval-api (port 8020)

```
POST /query           body: {query, scope?, top_k?, mode?}       → streamed SSE
POST /classify        body: {query}                              → {scope_label, intent, confidence}  (internal)
POST /scope           body: {query, history?}                    → {doc_ids, folder_paths, page, section, confidence}  (internal, deterministic)
GET  /documents/search?q=…                                       → filename/title search for the UI scope chip
GET  /models          list of currently available LLMs
GET  /healthz  /metrics
```

#### 7.3.1 Scope extraction (pre-classification, deterministic, LLM-free)

Before any model call, a rule-based extractor reads the current query + the last 3 turns of conversation and produces a `Scope` object (see **ADR-009**):

- **Explicit UI scope chip** (forwarded from Open WebUI as `x-reineke-scope` header) always wins.
- **Filename tokens** `\b[\w\-._]+\.(pdf|docx|xlsx|doc|xls|pptx|pptx)\b` → lookup in Postgres `rag.documents.filename` (exact first, then `pg_trgm` similarity ≥ 0.6 for fuzzy: "Angebot September" → `Angebot-2024-09.pdf`).
- **Inherited doc set** from the previous turn, *iff* the new turn is a short follow-up (≤ 15 tokens, no new file mention, no reset phrase).
- **Page / section tokens** `Seite (\d+)`, `page (\d+)`, `§ ?([\d.]+)` → `scope.page` / `scope.section_prefix`.
- **Folder references** like `/qms/...` or `im Ordner X` → `scope.folder_paths` (resolved against `rag.folders`).

Output (all fields optional):

```python
Scope(doc_ids=[UUID], folder_paths=[str], page=int, section_prefix=str, confidence=float)
```

If `confidence < SCOPE_MIN_CONFIDENCE` (default 0.5), scope falls back to corpus-wide and the UI is hinted: *"Searching all documents — click to scope to a file."*

#### 7.3.2 Updated classifier

The fast LLM outputs **two orthogonal labels** with a single prompt:

- `scope_label ∈ {single-doc, single-folder, multi-doc}` — advisory; overridden by deterministic scope extractor when `scope.confidence ≥ 0.5`.
- `intent ∈ {lookup, extraction, summarize, table-math, synthesis}` — authoritative.

#### 7.3.3 Five execution paths (dispatcher)

| Effective scope | Intent | Execution |
|-----------------|--------|-----------|
| single-doc | lookup | Hybrid search with `doc_id` + ACL filter, rerank top 30 → top 8. Fast tier. |
| single-doc | extraction | **Fetch ALL chunks** of the doc ordered by `ord`. If total ≤ `FULL_DOC_CONTEXT_THRESHOLD` (default 20 480 tokens): pass the full doc. Else: map-reduce per section. No rerank. Reasoning tier. |
| single-doc | summarize | Same as extraction but summary-oriented prompt. Reasoning tier. |
| single-doc | table-math | DuckDB SQL filtered to this doc's tables only; context drawn from doc's chunks. Reasoning tier. |
| single-folder | \* | Hybrid + rerank with `folder_path` prefix in the filter. Otherwise multi-doc behaviour for the given intent. |
| multi-doc | lookup / extraction | Hybrid + rerank, 12 results. Fast or Reasoning tier. |
| multi-doc | synthesis | Hybrid + rerank top 20; map-reduce over clusters by `doc_id`. Heavy tier (or Reasoning if heavy unavailable; see ADR-004). |
| multi-doc | table-math | Query-planner LLM sees schemas of all DuckDB tables the user may read; DuckDB executes; text context from hybrid search. Reasoning tier. |

Every cell has exactly one handler in `services/retrieval-api/handlers/`. The dispatcher is a small switch on `(scope, intent)`. No implicit fallthrough.

#### 7.3.4 SSE event contract

```
event: scope
data: {"doc_ids":["…"], "doc_names":["Angebot-2024-09.pdf"], "confidence":0.92}

event: plan
data: {"intent":"extraction","scope_kind":"single-doc","path":"full-doc-context","model":"qwen2.5:32b"}

event: citations
data: [{"doc_id":"…","chunk_id":"…","page":3,"score":0.82,"rerank":0.91,"preview":"…"}]

event: sql                 // only on table-math paths
data: {"sql":"SELECT ... FROM raw.t_… WHERE …","rows":[[…],[…]],"columns":[…]}

event: token
data: "Die "
…
event: done
data: {"tokens_in":2041,"tokens_out":273,"model":"qwen2.5:32b","latency_ms":4120,"trace":"lf-trace-abc"}
```

The UI always renders the `scope` and `plan` events as a small "what I'm doing" banner above the answer — users see at a glance that the system understood their intent.

#### 7.3.5 Retrieval algorithm (dispatcher, unchanged shape but scope-aware)

```
query(q, user, ui_scope=None):
    scope = extract_scope(q, conversation_tail(3), ui_scope)         # LLM-free
    labels = classify(q)                                             # fast LLM
    effective_scope = reconcile(scope, labels.scope_label)           # deterministic wins
    handler = dispatcher[(effective_scope.kind, labels.intent)]
    acl = AclFilter(groups=user.groups)
    emit("scope", effective_scope.public())
    emit("plan", handler.plan())
    for event in handler.run(q, effective_scope, acl):               # yields citations, sql, tokens
        emit(event)
    emit("done", stats())
    log_audit(...)
    langfuse.trace(...)
```

Handler example (single-doc / extraction):

```
handle_single_doc_extraction(q, scope, acl):
    chunks = db.chunks_for(scope.doc_ids[0], order_by=ord)           # ACL re-checked
    total_tokens = sum(c.token_count for c in chunks)
    if total_tokens <= FULL_DOC_CONTEXT_THRESHOLD:
        context = render_full_doc(chunks)                            # with section markers
        answer = llm.generate(reasoning_model, extraction_prompt(q, context))
    else:
        partials = [llm.generate(reasoning_model, extraction_prompt(q, section)) for section in split_by_h2(chunks)]
        answer = llm.generate(reasoning_model, aggregate_prompt(q, partials))
    citations = [c.citation() for c in chunks if c.id in cited_ids(answer)]
    yield "citations", citations
    yield from stream(answer)
```

**Anti-hallucination system prompt (abbreviated, EN; the real file has DE+EN bilingual):**

```
You are Reineke-RAG. You answer ONLY from the <context> blocks below.
Every factual claim MUST be followed by a bracketed citation like [1], [2]
referencing the citation list.
If the context does not contain the answer, say so explicitly in the user's
language and suggest what document type might contain it. Do not invent.
Preserve numbers exactly as given. Quote table cells verbatim.
```

### 7.4 duckdb-api (port 8030)

```
POST /register       (internal only) register a table from ingestion worker
POST /sql            body: {query_id, user_id, sql}       → {rows, columns}
GET  /tables         list tables visible to the caller
GET  /healthz  /metrics
```

Safety: `POST /sql` passes the SQL through a DuckDB parser first; rejects any statement that isn't a single `SELECT` (no `ATTACH`, no `COPY`, no `INSTALL`, no UDF calls that aren't on a whitelist). ACL enforced by rewriting `FROM raw.t_xyz` → `FROM views.v_xyz_<group>` where the view has a `WHERE` baked in — generated on first access per user's groups.

### 7.5 docling service (port 8001)

Thin wrapper around the Docling library. Stateless. Inputs a file (multipart); returns `DoclingDocument` JSON plus a flat list of tables (for XLSX). Runs with EasyOCR enabled by default; Tesseract fallback.

### 7.6 Open WebUI pipeline

Open WebUI's "Pipelines" feature lets us register a custom pipeline that intercepts the chat completion and calls our retrieval-api instead of Ollama directly. File `config/pipelines/reineke_rag.py`:

```python
# Pseudocode
class Pipeline:
    id = "reineke-rag"
    name = "Reineke-RAG"
    def pipe(self, user_message, messages, body, user):
        jwt = user["jwt"]            # forwarded by Open WebUI
        resp = requests.post(f"{RETRIEVAL_API}/query",
                             json={"query": user_message},
                             headers={"Authorization": f"Bearer {jwt}"},
                             stream=True)
        for event, data in parse_sse(resp):
            if event == "token":   yield data
            if event == "citations": yield render_citations(data)
```

## 8. Observability

**Langfuse** traces are emitted from retrieval-api for every query: a parent span with children for `classify`, `rewrite`, `dense_search`, `sparse_search`, `rerank`, `sql_plan`, `sql_exec`, `generate`. Each span carries the model name, inputs, outputs, latency and token counts. This is the **primary** debugging surface for retrieval quality.

**Prometheus** scrapes `/metrics` from every custom service plus node-exporter on the host. Key metrics:

- `rag_query_total{class}` counter
- `rag_query_latency_seconds{phase,class}` histogram
- `rag_retrieval_hits{source}` counter (dense/sparse/rerank)
- `rag_ingestion_jobs_total{state}` gauge
- `rag_doc_count`, `rag_chunk_count` gauges (from Postgres, refreshed every 60 s)

**Grafana** dashboards (shipped as provisioned JSON):

1. *Overview* — QPS, p50/p95 latency by class, error rate, model in use.
2. *Ingestion* — queue depth, job state pie, throughput MB/s, parse failures by mime type.
3. *Infra* — container CPU/RAM, disk on volumes, network.
4. *Quality* — (manual import from Langfuse) daily rerank uplift, top failing queries.

**Loki** collects all container stdout. Promtail tails docker logs. Retention 14 days for INFO, 90 days for WARN+.

## 9. Deployment topology

- Single machine, single `docker-compose.yml`, profiles:
  - (default) → core stack (services 1–23).
  - `automation` → adds n8n + watcher (services 24–25).
  - `minimal` → skips Langfuse + Loki + Grafana (for initial smoke test).

- For **multi-host growth** (v1.x, not v1): split onto two hosts — `worker` host runs Ollama + TEI + ingestion-worker (GPU/Metal heavy); `app` host runs everything else. Communication over the Docker overlay network or wireguard. Not delivered in v1.

## 10. Configuration surface (highlights)

Everything in `.env`; see `config/.env.example` for the full list. Highlights:

```ini
DATA_ROOT=/var/lib/reineke
CONFIG_ROOT=/etc/reineke
PRIMARY_DOMAIN=rag.reineke.local

# Auth
AUTHENTIK_SECRET_KEY=...
OIDC_CLIENT_ID_OPENWEBUI=...
OIDC_CLIENT_SECRET_OPENWEBUI=...

# Models
LLM_FAST=gemma2:9b-instruct-q5_K_M
LLM_REASONING=qwen2.5:32b-instruct-q4_K_M
LLM_HEAVY=llama3.3:70b-instruct-q4_K_M
EMBED_MODEL=bge-m3
RERANK_MODEL=BAAI/bge-reranker-v2-m3

# Retrieval
TOP_K_DENSE=50
TOP_K_SPARSE=50
TOP_K_RERANK=12
HYBRID_FUSION=rrf
QUERY_REWRITE=true

# Chunking
CHUNK_MAX_TOKENS=512
CHUNK_MIN_TOKENS=128
PRESERVE_TABLES=true

# ACL defaults
DEFAULT_FOLDER_ACL=admin
```

All custom services read the same `.env` (via `docker-compose` → container envs). Changing chunking or retrieval constants triggers an advisory note on `make up`: "re-index recommended for new ingestions to benefit".

## 11. Backup & disaster recovery (architectural)

Operational detail in `04_OPERATIONS.md`. Architecturally:

- **One cron job** runs `scripts/backup.sh` nightly.
- Backups, per source:
  - `postgres`, `authentik-db`, `langfuse-db` → `pg_dump -Fc`.
  - `minio` → `mc mirror` to `${BACKUP_ROOT}/minio/`.
  - `qdrant` → snapshot API → compress → `${BACKUP_ROOT}/qdrant/`.
  - `duckdb` → file copy (single file).
  - `redis` AOF → file copy.
- Retention: 7 dailies, 4 weeklies, 12 monthlies (GFS).
- Restore is explicitly rehearsed in Phase 8 (see `05_IMPLEMENTATION_PLAN.md`).
- Encryption at rest: filesystem-level (`zfs encryption` or LUKS) — not app-level. Documented in ops.

## 12. Extensibility points

Documented so v1.x additions don't require an architecture rewrite:

1. **New mime types** → add a parser method to docling service (most already supported; add PPTX, HTML, MD as needed).
2. **New LLMs** → `ollama pull`; add to `LLM_*` env; optionally extend `pick_model()`.
3. **New embedder** → swap `EMBED_MODEL`; new Qdrant collection needed (vector size differs); reindex job handles it.
4. **New ACL predicates** (e.g. per-document tags, confidentiality level) → add payload field + filter clause; migrations script to backfill.
5. **New data sources** (wiki, mail) → add a connector service that normalises to `DoclingDocument`-like JSON and submits to ingestion-api.

## 13. Known limitations & mitigations

| Limitation | Mitigation |
|------------|------------|
| Ollama has no built-in auth. | Not exposed beyond Docker network; bind to `127.0.0.1` on host if running non-Docker. |
| DuckDB is single-writer. | Writers are only ingestion-worker + duckdb-api; retrieval reads via HTTP, not by opening the file. |
| bge-reranker is a cross-encoder — latency grows with candidates. | Hard cap at 50 candidates into reranker; candidates from dense+sparse already deduped. |
| Docling scanned-PDF quality is OCR-bound. | EasyOCR is default; Tesseract+German traineddata is configurable. We do not promise 100 % OCR accuracy. |
| n8n RAG nodes were explicitly rejected. | We do not use them. If users want to add n8n flows that call our retrieval-api, they can — we expose a standard HTTP endpoint. |
| No horizontal scaling in v1. | Architecture separates heavyweight (Ollama/TEI/workers) from the rest; a two-host split is documented as the v1.1 path. |

## 14. Versioning & compatibility

- Semver for the **stack as a whole** (v1.0.0 at first cut).
- **Embedding model change** is a breaking change (reindex required); bumps the MAJOR of the stack.
- **Chunking config change** that alters chunk boundaries is also breaking; bumps MAJOR.
- **Prompt changes**, **LLM swaps**, **UI changes** → MINOR or PATCH.
- The `rag` Postgres schema has its own migration history in `migrations/` (Alembic).
- Qdrant collection creation is idempotent and checked on startup; a new collection name is introduced on any vector-shape change (blue/green).

---

The next document, `05_IMPLEMENTATION_PLAN.md`, turns all of the above into a sequence of buildable phases with acceptance criteria. `06_AGENT_BRIEFS.md` then assigns each piece to a specialised subagent.
