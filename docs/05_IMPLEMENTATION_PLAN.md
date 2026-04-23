# 05 — Implementation Plan

> This plan is written for an AI **coordinator agent** that delegates to specialist subagents (see [06_AGENT_BRIEFS.md](06_AGENT_BRIEFS.md)). Every phase has an **owner subagent**, **inputs**, **deliverables**, and **acceptance criteria**. If acceptance fails, the phase loops before the next one starts.

---

## Prerequisites — Phase 0

**Owner:** `coordinator` + owner (human).
**Goal:** answer the open questions from `01_CONCEPT.md` § 12 and provision the host.

Inputs the coordinator must gather before Phase 1:

| # | Item | Default if not answered |
|---|------|-------------------------|
| P0.1 | Folder taxonomy (paths + groups) | One root `/all/` readable by all groups |
| P0.2 | Group list | `admin, engineering, qms, sales, finance, hr, guest` |
| P0.3 | `PRIMARY_DOMAIN` for Caddy | `rag.reineke.local` |
| P0.4 | Backup target | `/Volumes/Backup/reineke` |
| P0.5 | HTTPS strategy | Local internal CA via Caddy |
| P0.6 | Keep n8n in v1? | No — add later |
| P0.7 | Host OS | macOS 15+ on M4 Max |
| P0.8 | Sample corpus (10–50 docs) for Phase 8 eval | Owner must provide |

The coordinator MUST confirm these with the owner before proceeding. It records them in `config/owner-inputs.yaml`.

Host prep checklist (the coordinator issues these as a script, human executes):

```
brew install docker docker-compose colima        # if no Docker Desktop
colima start --cpu 10 --memory 48 --disk 200     # leave 16 GB for macOS + other apps
mkdir -p /var/lib/reineke /etc/reineke
sudo chown $USER /var/lib/reineke /etc/reineke
```

**Exit criteria for Phase 0:** `owner-inputs.yaml` populated, Colima/Docker running, `/var/lib/reineke` writable, 200 GB free on disk. **On macOS (Apple Silicon) the bootstrap script also verifies that native Ollama is installed and reachable at `:11434` (see ADR-010); it refuses to advance to Phase 1 until that's true (acceptance A0.9).**

---

## Phase 1 — Foundations

**Owner:** `deployment-agent`.
**Depends on:** P0.
**Goal:** bootable Docker Compose with the non-custom services only. No app code yet.

Scope:

- Generate `docker-compose.yml` with services 1, 6, 7, 8, 9, 21–23 from `02_ARCHITECTURE.md` §1 plus Caddy.
- Generate `.env.example` with every variable.
- Generate Caddyfile for basic routing.
- Volumes, network, healthchecks wired for all services.
- `make up`, `make down`, `make logs`, `make ps` targets.

Deliverables:

- `docker-compose.yml`
- `.env.example`, `.env` generated from it (secrets filled)
- `config/caddy/Caddyfile`
- `Makefile`
- `scripts/bootstrap.sh`

Acceptance criteria:

- A1.1 `make up` brings up all services; all `docker ps` healthchecks green within 2 min.
- A1.2 `https://${PRIMARY_DOMAIN}/qdrant/dashboard/` reaches Qdrant UI through Caddy with the configured API key.
- A1.3 `psql` via Docker exec connects to `postgres` and lists the empty `rag` schema (created by init SQL).
- A1.4 `mc alias set local http://minio:9000 …; mc ls local/` works; the `raw` bucket exists.
- A1.5 Prometheus scrapes at least node + postgres + minio; Grafana shows data on the "Infra" dashboard.

---

## Phase 2 — Identity

**Owner:** `auth-agent`.
**Depends on:** Phase 1.
**Goal:** Authentik deployed, admin account created, groups provisioned, an OIDC application wired to a test client (jwt.io or a one-file Python tester).

Scope:

- Add `authentik-server`, `authentik-worker`, `authentik-db`, `authentik-redis` to Compose.
- Apply a blueprint (Authentik YAML) that:
  - Creates the groups listed in `owner-inputs.yaml`.
  - Creates OIDC applications for: `openwebui`, `retrieval-api`, `ingestion-api`, `duckdb-api`, `langfuse`, `grafana`, `n8n` (the last inactive in v1).
  - Creates one seed admin user with a first-login password reset flow.
- Configure Caddy to protect Authentik at `/auth`.

Deliverables:

- `config/authentik/blueprints/bootstrap.yaml`
- `docs/auth-setup.md` (short admin guide extracted into `04_OPERATIONS.md`)
- Secrets written to `.env`: `OIDC_CLIENT_ID_*`, `OIDC_CLIENT_SECRET_*`, `AUTHENTIK_SECRET_KEY`, `AUTHENTIK_BOOTSTRAP_PASSWORD`.

Acceptance criteria:

- A2.1 First login at `https://${PRIMARY_DOMAIN}/auth` as bootstrap admin works, password change flow triggers.
- A2.2 A test Python script (`scripts/oidc-test.py`) does the OIDC client-credentials flow against `retrieval-api`'s client and receives a JWT with a `groups` claim populated from group membership.
- A2.3 The JWT's signing key is retrievable at `/auth/.well-known/openid-configuration` → JWKS.

---

## Phase 3 — Models & embedding/rerank services

**Owner:** `llm-agent`.
**Depends on:** Phase 1.
**Goal:** Ollama + TEI reranker running and serving the defined models; smoke tests green.

Scope:

- Add `ollama` and `tei-reranker` to Compose.
- `scripts/pull-models.sh` pulls:
  - `bge-m3` (embedding).
  - `gemma2:9b-instruct-q5_K_M` (fast).
  - `qwen2.5:32b-instruct-q4_K_M` (reasoning).
  - `llama3.3:70b-instruct-q4_K_M` (heavy; optional on constrained hosts via env flag `PULL_HEAVY=false`).
- TEI launched with `model-id BAAI/bge-reranker-v2-m3`.
- Probe scripts for each endpoint.

Deliverables:

- `scripts/pull-models.sh`
- `scripts/smoke-llm.sh` — curls every model with a canned prompt.
- `scripts/smoke-embed.sh` — embeds a DE + EN test sentence, checks vector length == 1024.
- `scripts/smoke-rerank.sh` — pairs a query + 5 candidates, checks `rerank_score` monotone.

Acceptance criteria:

- A3.1 All smoke scripts exit 0.
- A3.2 `ollama list` shows all pulled models, `ollama ps` shows 0 loaded initially.
- A3.3 Latency sanity: gemma2 9b ≤ 2 s to first token, qwen2.5 32b ≤ 6 s, (optional) llama 70b ≤ 20 s (reference box, native Ollama).
- A3.4 Reranker p95 for 12 candidates + 1 query ≤ 500 ms (TEI on Linux GPU, or sentence-transformers MPS on Apple Silicon per ADR-010).
- A3.5 Measured tokens/second on each tier is within 50 % of the ADR-004 reference figures; a miss means Ollama is running CPU-only (catches a mis-install on macOS).

---

## Phase 4 — Docling service

**Owner:** `ingestion-agent` (part 1 of its domain).
**Depends on:** Phase 1.
**Goal:** a standalone parsing service behind HTTP.

Scope:

- Dockerfile for `services/docling/` (Python 3.12, `docling`, `easyocr`, `tesseract-deu`).
- FastAPI wrapper: `POST /parse` (multipart) → `DoclingDocument` JSON + list of tables; `GET /healthz`; `/metrics`.
- Pre-download OCR models at image build time (so ingestion never does network pulls at runtime).

Deliverables:

- `services/docling/Dockerfile`
- `services/docling/app.py`
- `services/docling/requirements.txt`
- `services/docling/tests/` with fixtures: 3 PDFs (1 text, 1 scanned, 1 with tables), 2 DOCX, 2 XLSX.

Acceptance criteria:

- A4.1 All fixture files parse without error; returned JSON includes section paths and table structures.
- A4.2 Scanned PDF fixture produces `> 90 %` of the ground-truth text (diff against a hand-extracted `.txt`).
- A4.3 XLSX fixture returns all sheets with correct row/column counts.
- A4.4 Parsing a 30-page mixed PDF completes in < 60 s on the reference box.

---

## Phase 5 — Ingestion pipeline end-to-end

**Owner:** `ingestion-agent` (part 2).
**Depends on:** Phases 2, 3, 4.
**Goal:** upload → parse → chunk → embed → index works end-to-end, including tables to DuckDB.

Scope:

- `services/ingestion-api/` (FastAPI) — endpoints per §7.1 of architecture.
- `services/ingestion-worker/` — RQ worker, pipeline per §7.2.
- `services/duckdb-api/` — registration endpoint + SQL executor per §7.4.
- Postgres migrations (Alembic) for the `rag.*` schema.
- Folder ACL CRUD API.
- Minimal admin CLI (`rag-admin`) — Python entry point that hits the ingestion API with an admin JWT for batch ops.

Deliverables:

- `services/ingestion-api/app.py`, worker module.
- `services/duckdb-api/app.py`.
- `migrations/0001_init.sql` and the alembic chain.
- `scripts/seed-folders.sh` (from `owner-inputs.yaml` to folder rows).
- End-to-end test `tests/e2e_ingest.py`.

Acceptance criteria:

- A5.1 Ingesting the Phase 4 fixtures via `POST /documents` (with appropriate `folder_path`) results in `status=indexed` within 2 min each.
- A5.2 Qdrant `chunks` collection contains non-zero points per doc; payload includes `acl_groups`, `section_path`.
- A5.3 DuckDB `meta.catalog` has one row per XLSX sheet; sampling `SELECT * FROM raw.t_…` returns expected rows.
- A5.4 Dedupe: re-uploading the same file to the same folder returns `409` with the prior `doc_id`.
- A5.5 Re-indexing a doc clears old chunks and creates fresh ones (idempotent).
- A5.6 Changing a folder's ACL fires a payload-rewrite job that updates every affected Qdrant point's `acl_groups` within 30 s for 1 000 chunks.

---

## Phase 6 — Retrieval & generation

**Owner:** `retrieval-agent`.
**Depends on:** Phases 3, 5.
**Goal:** end-to-end query → streamed answer with citations, including the **single-document** paths specified in ADR-009.

Scope:

- `services/retrieval-api/` (FastAPI) per §7.3.
- **Deterministic scope extractor** (`services/retrieval-api/scope.py`) per §7.3.1 — filename / fuzzy / follow-up inheritance / page / section / folder.
- Classifier prompt emits both `scope_label` and `intent` per ADR-009.
- **Dispatcher + 5 handlers** under `services/retrieval-api/handlers/` (single-doc × {lookup, extraction, summarize, table-math} + multi-doc base).
- Query-rewrite prompt (applied only on multi-doc paths).
- Hybrid search against Qdrant (native prefetch + RRF fusion) with ACL filter baked in.
- Reranker call (TEI on Linux / sentence-transformers MPS on Apple Silicon).
- SQL-path branch through `duckdb-api`, scope-filtered on single-doc paths.
- Full-doc context path (concat all chunks if total ≤ `FULL_DOC_CONTEXT_THRESHOLD`; else map-reduce per section).
- Langfuse SDK wiring for tracing (scope + plan + citations + SQL + tokens as spans).
- System prompt templates (`config/retrieval/prompts/{de,en,bilingual}/{lookup,extraction,summarize,table-math,synthesis}.md`).
- `GET /documents/search` for the UI scope chip.

Deliverables:

- `services/retrieval-api/`
- `config/retrieval/prompts/`
- `tests/e2e_query.py` — ≥ 15 canned queries covering **all 5 intents × single-doc and multi-doc scopes**, asserts on cited doc_ids.
- `tests/regressions/single_doc/` — starter regressions for single-doc paths.

Acceptance criteria:

- A6.1 All canned queries produce cited answers; citations point to correct doc_ids.
- A6.2 ACL: a token without group `qms` running a query whose gold chunk is in `/qms/` gets an "information not available" response; zero `/qms/` chunks appear in `citations` or in the `scope` event; applies to all 5 intents.
- A6.3 Table-math query: "Welche Zeile hat den größten Wert in Spalte X der Datei Y.xlsx?" emits a `sql` event (with the executed statement + rows) and a cited answer.
- A6.4 Langfuse shows a trace per query with `scope`, `classify`, handler-specific spans, and `generate`.
- A6.5 p95 latency for `multi-doc lookup` class ≤ 5 s to last token (fast tier, native Ollama on reference host).
- **A6.6 Filename-anchored extraction**: *"Liste alle Lieferfristen aus {file}.pdf"* returns a list whose recall against hand-extracted ground truth is ≥ 95 %. Scope event reports `confidence ≥ 0.9` and the correct `doc_id`.
- **A6.7 Full-doc summary**: for a ≤ 15 k-token DOCX, the summary contains all 5 hand-picked gold points. Judged by a separate LLM-as-judge call against a ground-truth bullet list (prompt in `tests/fixtures/judge_summary.md`); pass threshold ≥ 4/5.
- **A6.8 Scope chip propagation**: when the UI forwards the chip header, every Qdrant call made by the handler carries `doc_id` filter. Verified by Langfuse span inspection; a chip-scoped query never surfaces a corpus-wide chunk in `citations`.
- **A6.9 Page-anchored query**: *"Was steht auf Seite 3 von {file}.pdf?"* returns only chunks where `payload.page = 3` AND `payload.doc_id = target`. Test asserts the filter dict, not just the rendered output.
- **A6.10 Follow-up inheritance**: after a single-doc turn, a short follow-up without a file mention inherits the doc_id; a reset phrase ("neue Frage", "new question", "andere Datei") clears it. Test asserts scope event contents on two consecutive turns.

---

## Phase 7 — UI

**Owner:** `ui-agent`.
**Depends on:** Phases 2, 6.
**Goal:** Open WebUI, OIDC login, custom pipeline routes all chat via retrieval-api.

Scope:

- Add `openwebui`, `pipelines` to Compose.
- Configure OIDC (redirect URI from Authentik).
- Install custom pipeline `pipelines/reineke_rag.py`.
- Hide direct LLM selection (users don't pick models; the router does) — configure Open WebUI's model list to expose only `Reineke-RAG`.
- Customise theme: app name, logo slot, language toggle (DE/EN).
- Enable citation rendering (pipeline emits a compact `[1]` list; UI renders clickable previews).

Deliverables:

- `config/openwebui/config.json`
- `config/pipelines/reineke_rag.py`
- `config/openwebui/theme/` (optional, minimal)

Acceptance criteria:

- A7.1 Opening `https://${PRIMARY_DOMAIN}/` redirects to Authentik, login returns to chat UI.
- A7.2 User groups from JWT propagate to pipeline; same query by user-A and user-B with different groups returns different citations.
- A7.3 Clicking a citation opens a signed MinIO URL preview highlighted to the page.
- A7.4 Admin view (`/admin`) is only accessible to users in `admin` group.

---

## Phase 8 — Quality & hardening

**Owner:** `retrieval-agent` + `ingestion-agent` (shared).
**Depends on:** Phase 7.
**Goal:** verify success criteria from `01_CONCEPT.md` §9 with the owner's real corpus.

Scope:

- Owner supplies 100 real documents (mixed PDF/DOCX/XLSX/DE/EN). Ingest them all.
- Build a **gold eval set of ≥ 100 queries**, partitioned by the categories that matter for this product. Owner authors at least the single-doc bucket (these are the queries that dominate daily use); coordinator drafts the rest for owner review:

  | Bucket | Target | Rationale |
  |--------|--------|-----------|
  | single-doc lookup | 25 | Short factual Q within one file |
  | single-doc extraction | 10 | "List all X from Y.pdf" — recall is king |
  | single-doc summarize | 5 | Full-doc-context path |
  | single-doc table-math | 10 | SQL path with `doc_id` filter |
  | multi-doc lookup | 20 | Classic hybrid retrieval |
  | multi-doc table-math | 10 | SQL path over many tables |
  | synthesis | 10 | Heavy-tier path |
  | ACL leak probes | 10 | Queries where the gold chunk sits in a folder the probing user cannot read |

- Run `scripts/eval.py` — prints per-bucket recall@3, recall@10, citation fidelity, and latency.
- Create Grafana "Quality" dashboard that ingests Langfuse exports.
- Ship a `docs/eval/baseline-$(date).md` report.

Acceptance criteria:

- A8.1 Meet all success criteria in `01_CONCEPT.md` §9, measured per bucket:
  - single-doc lookup + extraction + table-math: recall@10 ≥ 95 %, citation fidelity ≥ 95 %.
  - multi-doc lookup: recall@10 ≥ 90 %.
  - ACL leak probes: 0 leaks across all 10.
- A8.2 Baseline report committed. Known regressions per bucket enumerated with severity.
- A8.3 At least 5 representative "failing" queries captured as regression tests in `tests/regressions/`, mirroring bucket distribution.
- A8.4 The four success criteria that explicitly apply to single-doc work (§9 items 1, 3, 4 plus the new "filename-anchored query recall" derived from A6.6) are each measured and pass.

---

## Phase 9 — Operational readiness

**Owner:** `deployment-agent` + `observability-agent`.
**Depends on:** Phase 8.
**Goal:** system is operable for a small IT team.

Scope:

- `scripts/backup.sh` and `scripts/restore.sh` per `02_ARCHITECTURE.md` §11.
- `scripts/rotate-secrets.sh`.
- Launchd/systemd unit for nightly backup.
- Loki + Promtail wiring; 14-day retention configured.
- Grafana alert rules: disk free < 10 %, any container unhealthy for 5 min, ingestion queue backlog > 200.
- `docs/04_OPERATIONS.md` reviewed against reality — every command present actually works.

Acceptance criteria:

- A9.1 Backup + **restore rehearsal** passes: wipe `/var/lib/reineke`, restore from last night's backup, all data + ACLs + models intact; a sample query works unchanged.
- A9.2 Killing any single non-DB container auto-recovers via `restart: unless-stopped`.
- A9.3 Pulling the LAN cable mid-query makes the system degrade gracefully (no outbound calls, no crashes).
- A9.4 Onboarding a fresh admin user via `04_OPERATIONS.md` alone takes < 30 min.

---

## Phase 10 — Handover

**Owner:** `coordinator`.
**Depends on:** all.

Scope:

- Tag `v1.0.0`.
- Produce `HANDOVER.md`: short summary of what was built, known limitations, next steps, all passwords (pointer into the admin's password manager, not literal secrets).
- Walk-through video / screen recording optional, text handbook mandatory.

Acceptance criteria:

- A10.1 Owner confirms they can (i) add a user, (ii) add a folder, (iii) ingest a new document, (iv) ask a question and see a correct cited answer, (v) read a Grafana dashboard, **without help**.

---

## Dependency graph

```
P0 ─► P1 ─┬─► P2 ─┐
          ├─► P3 ─┤
          ├─► P4 ─┴─► P5 ─► P6 ─► P7 ─► P8 ─► P9 ─► P10
          │              ▲
          └──────────────┘  (P5 needs P2 for JWT verification, P3 for embed, P4 for parse)
```

Indicators to **run phases in parallel where possible**:

- P2, P3, P4 are independent after P1 and can be executed by `auth-agent`, `llm-agent`, `ingestion-agent` concurrently.
- Within P5, the SQL-path (`duckdb-api`) and main ingestion are independent and can be split.

## Budget & time estimate

| Phase | Rough effort (engineer-days) |
|-------|------------------------------|
| P0 | 0.5 |
| P1 | 1.5 |
| P2 | 1.5 |
| P3 | 1.0 (mostly wait + pulls) |
| P4 | 2.0 |
| P5 | 3.0 |
| P6 | 3.0 |
| P7 | 1.5 |
| P8 | 2.0 |
| P9 | 1.5 |
| P10 | 0.5 |
| **Total** | **~18 engineer-days** |

For an AI-agent build, wall-clock time scales with the number of parallelisable phases and human-in-the-loop gates. A reasonable estimate is 3–5 wall-clock days if the owner is responsive on the Phase 0 and Phase 8 loops.

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Docling parses a real doc badly | Medium | High | Phase 8 eval catches; fallback to PyMuPDF+PDFPlumber behind a feature flag. |
| 70 B model too slow on M4 Max | Medium | Low | Route 70 B only for explicit synthesis; monitor timeouts. |
| Authentik upgrade breaks OIDC | Low | High | Pin minor version; backup auth-db nightly; smoke-test after each upgrade. |
| Qdrant version changes fusion API | Low | Medium | Pin `v1.12.0`; upgrade path tested in a staging dir. |
| User uploads secrets-rich XLSX (passwords, PII) | Medium | High | Document classification feature flagged for v1.1; meanwhile rely on folder ACL + audit log. |
| Backup job silently fails | Low | Catastrophic | Backup script emits Prometheus metric; Grafana alert on missed run. |

## Definition of Done (for the overall build)

All Phase acceptance criteria green, **and** all Concept §9 success criteria measured on owner's real corpus, **and** Owner has countersigned `HANDOVER.md`. The stack is then `v1.0.0`.
