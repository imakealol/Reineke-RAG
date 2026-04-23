# ADR-002 — Qdrant for vector + sparse retrieval

- **Status:** Accepted
- **Date:** 2026-04-23

## Context

We need a vector store that supports:

1. **Hybrid retrieval** (dense + sparse in a single query).
2. **Payload filtering** for ACL enforcement (mandatory filter on every query).
3. Scale to ~5 M vectors without heroics.
4. Runs in a single container, open-source license, mature authentication.

## Options considered

| Option | Hybrid? | ACL filter | Ops | License | Notes |
|--------|---------|------------|-----|---------|-------|
| **Qdrant** 1.12+ | Native (dense + sparse + RRF/DBSF) | Fast; indexed payload fields | Single container, Rust core, API key + JWT | Apache 2.0 | Hybrid shipped in Aug 2024; production-ready. |
| pgvector (on existing Postgres) | Dense only out-of-box; hybrid via bm25/rum extensions | SQL-native | zero extra ops | PostgreSQL | Fine for small corpora; hybrid setup is fiddly. |
| Weaviate | Yes | Yes | heavier (Go + modules) | BSD-3 | Good; more moving parts than we need. |
| Milvus | Yes (since 2.4) | Yes | Needs etcd + MinIO + multiple svc | Apache 2.0 | Designed for billion-scale; overkill here. |
| Chroma | Dense only | basic | simple | Apache 2.0 | No hybrid; weak at our scale. |
| Elasticsearch + ELSER | Yes | Yes | Heavyweight JVM | Elastic 2.0 | License + memory pressure; strong if already in the org (we're not). |

## Decision

**Qdrant 1.12+** pinned in the Compose file. We use:

- **Named vectors**: `dense` (1024-d cosine) for bge-m3 dense output.
- **Sparse vectors**: a `sparse` slot fed by bge-m3's sparse output (weighted token scores).
- **Reciprocal Rank Fusion (RRF)** at query time via Qdrant's `FusionQuery`.
- **Indexed payload fields**: `acl_groups`, `folder_path`, `content_type`, `language`, `doc_id`.
- **Scalar int8 quantization** for RAM efficiency; `on_disk_payload=true`.

Authentication: API key on every client. The API key is held only by the custom services; it never reaches browsers.

## Consequences

Positive:

- One store for dense + sparse; no external Lucene/BM25 index to keep in sync.
- Filter-first semantics: we cannot forget the ACL filter — it sits in the same `search()` call, not on a downstream stage.
- Mature snapshot API simplifies backups.

Negative:

- Qdrant's hybrid API is newer than dense-only — we pin a specific version (1.12.0) and validate upgrades in staging.
- Two vector shapes (dense + sparse) are more config than dense-only; mitigated by centralising it in `services/retrieval-api/qdrant_client.py`.

## Migration / upgrade notes

- A vector dimension change (e.g. swapping embedder) requires a new collection (blue/green). A reindex job is the path.
- A payload schema change (new ACL field) is online — add the indexed field, run a payload update job.
