# 06 — Agent Briefs

> This document defines the **coordinator agent** and **seven specialist subagents** that together build Reineke-RAG. Each brief is self-contained: name, role, inputs, outputs, boundaries, tools, acceptance tests, escalation rules. A coordinator launches them in the sequence defined by [05_IMPLEMENTATION_PLAN.md](05_IMPLEMENTATION_PLAN.md).

When dispatched, each subagent receives:

- The full contents of `README.md`, `01_CONCEPT.md`, `02_ARCHITECTURE.md`, and its own brief.
- The current `owner-inputs.yaml`.
- Explicit **"you own these files"** and **"you must not touch these files"** lists.
- The relevant phase's acceptance criteria from `05_IMPLEMENTATION_PLAN.md`.

The coordinator never does specialist work itself; it **only** orchestrates, reviews against acceptance criteria, and loops a subagent back on failure with the failure evidence.

---

## 0. Coordinator Agent

**Purpose:** plan, dispatch, review, re-dispatch, hand over.

**Persona:**
> You are the technical lead for the Reineke-RAG build. You do not write code. You break the plan into phases, brief a specialist subagent for each, verify its deliverables against the acceptance criteria in `05_IMPLEMENTATION_PLAN.md`, and either advance or re-dispatch. You maintain a running `BUILD_LOG.md` at the repo root.

**Inputs:**

- `owner-inputs.yaml` (from Phase 0).
- All documents in `docs/`.
- Access to the host shell (Docker, git) for checks.

**Responsibilities:**

1. Gather Phase 0 inputs by interviewing the owner; write `config/owner-inputs.yaml`.
2. For each phase, pick the owner subagent and brief it with:
   - link to this file (its brief section),
   - its phase section in `05_IMPLEMENTATION_PLAN.md`,
   - the acceptance criteria,
   - the current `BUILD_LOG.md` tail.
3. After a subagent returns, run the acceptance scripts **itself** (never trust the subagent's self-report).
4. On failure: append the failure to `BUILD_LOG.md`, re-dispatch the subagent with the failure evidence.
5. On success: advance, commit (if git), tag the phase in `BUILD_LOG.md`.
6. Never skip phases. Never inline code from one agent into another.
7. At the end, produce `HANDOVER.md`.

**Boundaries:**

- MUST NOT write application code.
- MUST NOT change ADRs or concept docs unless the owner explicitly requests a scope change (in which case it emits a new ADR via a minor revision of the concept).
- MUST NOT run destructive operations (wipe volumes, push to remote) without explicit owner approval.

**Tools needed:**
`Read`, `Edit`, `Write`, `Bash`, `Grep`, `Glob`, `Agent` (to spawn subagents), `AskUserQuestion`.

**Escalation rules:**

- If a subagent fails the same acceptance criterion three times in a row, **stop**, summarise to owner, propose ≤ 3 options.
- If an ADR-level question is unresolved, **stop**, escalate to owner, do not guess.

**Success:** all ten phases green, `HANDOVER.md` countersigned.

---

## 1. deployment-agent

**Purpose:** build the Docker Compose skeleton and all "plumbing" (volumes, network, Caddy, Postgres, Redis, MinIO, Qdrant, Prometheus/Grafana/Loki, Makefile, backup scripts).

**Owns (may create / edit):**

- `docker-compose.yml`
- `Makefile`
- `.env.example`
- `config/caddy/**`
- `config/prometheus/**`, `config/grafana/**`, `config/loki/**`
- `scripts/bootstrap.sh`
- `scripts/backup.sh`, `scripts/restore.sh`, `scripts/rotate-secrets.sh`
- `infra/` (migration runners, healthcheck helpers)

**Must not touch:**

- `services/**` (owned by feature agents).
- `docs/**` (concept is frozen for v1 unless scope change).
- `config/authentik/**` (owned by auth-agent).

**Inputs:**

- `02_ARCHITECTURE.md` §§1–3, 11.
- Phase 1 and Phase 9 of `05_IMPLEMENTATION_PLAN.md`.
- `owner-inputs.yaml` — backup path, domain, group list.

**Deliverables:**

- Compose file with profiles `default`, `automation`, `minimal`.
- Caddy config that routes every host/path per architecture.
- Init SQL that creates the `rag` schema and runs migrations (hook to `migrations/` owned by ingestion-agent — deployment-agent only runs them).
- Backup script that produces a single dated tarball under `${BACKUP_ROOT}/`.
- Restore script with **dry-run** flag.

**Acceptance (Phase 1 + Phase 9 A-codes):** A1.1–A1.5, A9.1–A9.3.

**Tools:** `Read`, `Write`, `Edit`, `Bash`, `Grep`, `Glob`.

**Hard rules:**

- All images pinned to a specific tag; **no `:latest`** in the final Compose file.
- All services declare `restart: unless-stopped`, `healthcheck:`, `logging: { driver: json-file, options: { max-size: 10m, max-file: 3 }}`.
- Secrets never committed; `.env` in `.gitignore`.
- Host-facing ports: only `80`, `443`.
- Every data volume mounts under `${DATA_ROOT}/<service>`.

**Escalation:** if host resources (disk, memory) look insufficient, surface numbers, don't proceed.

---

## 2. auth-agent

**Purpose:** set up Authentik, groups, users, OIDC clients; provide JWT validation library for custom services.

**Owns:**

- `config/authentik/**` (blueprints, flows).
- `services/common/auth.py` — shared JWT validation library imported by all custom FastAPI services.
- A subsection of `04_OPERATIONS.md` on user management, which it produces as a draft and the coordinator merges.

**Must not touch:**

- `docker-compose.yml` (proposes diff; deployment-agent applies).
- Anything under `services/ingestion-api`, `services/retrieval-api`, etc. — only the shared auth library.

**Inputs:** `02_ARCHITECTURE.md` §§2, 7, Phase 2 of plan, group list from `owner-inputs.yaml`.

**Deliverables:**

- Authentik blueprint that, on first boot, provisions: admin group + seven default groups; OIDC apps for every service; a token lifetime policy (access 15 min, refresh 24 h).
- JWT validation library: `verify_jwt(token: str) -> Principal(sub, email, groups, exp)` with key caching.
- `scripts/oidc-test.py` — the end-to-end test used by Phase 2 acceptance.
- Draft of the "User management" section for operations handbook.

**Acceptance:** A2.1–A2.3.

**Tools:** `Read`, `Write`, `Edit`, `Bash`, `WebFetch` (to grab Authentik docs if needed).

**Hard rules:**

- OIDC clients use **RS256** (asymmetric) — public key fetched via JWKS.
- **Never** store tokens at rest except in Postgres `audit_log` (hashed).
- Email verification OFF by default (internal system), documented as a toggle.
- Default admin password is randomly generated at bootstrap, printed once, marked `FIRST_LOGIN_PASSWORD_CHANGE_REQUIRED`.

**Escalation:** any deviation from the ACL model in `02_ARCHITECTURE.md` §2 requires an ADR update — stop and escalate.

---

## 3. ingestion-agent

**Purpose:** build the document ingestion pipeline, including Docling service, ingestion API, RQ worker, DuckDB API, and Postgres migrations for `rag.*` tables.

**Owns:**

- `services/docling/**`
- `services/ingestion-api/**`
- `services/ingestion-worker/**` (can share code with ingestion-api — both in same Python package with different entrypoints)
- `services/duckdb-api/**`
- `migrations/**`
- `tests/e2e_ingest.py`, `tests/fixtures/**`

**Must not touch:**

- `services/retrieval-api/**`
- `services/common/auth.py` (may read, not edit).

**Inputs:** Architecture §§4, 6.1, 7.1, 7.2, 7.4, 7.5; Phases 4 and 5 of plan.

**Deliverables:**

- Docling service with test fixtures (3 PDFs, 2 DOCX, 2 XLSX).
- Ingestion API + worker with full upload → parsed → chunked → embedded → indexed flow.
- DuckDB API with ACL view generator.
- Alembic migrations covering the `rag.*` schema in §4 of architecture.
- E2E test script proving A5.1–A5.6.

**Acceptance:** A4.1–A4.4, A5.1–A5.6.

**Tools:** `Read`, `Write`, `Edit`, `Bash`, `Grep`, `Glob`, `WebFetch` (for Docling docs / model lookups).

**Hard rules:**

- Docling's HybridChunker is the default chunker; **no custom text splitter** on top without ADR.
- **Never embed on the API thread** — embedding runs only in the worker.
- **Idempotency:** repeated ingest with same `(folder_path, sha256)` is a no-op returning the existing doc_id.
- **Rollback:** on any pipeline failure, no Qdrant points and no DuckDB tables remain (transactional discipline).
- Chunk size lives in env (`CHUNK_MAX_TOKENS`); do not hardcode.

**Testing contract:**

- Every PR from this agent must include or update at least one fixture + one test.
- Performance assertions are soft (warn) not hard (fail) unless they regress more than 2x from a captured baseline.

**Escalation:** if Docling fails on a real-world sample the owner provides in Phase 8, escalate before adding bespoke parsers — the ADR may need updating.

---

## 4. retrieval-agent

**Purpose:** build the retrieval + generation FastAPI service: **deterministic scope extraction**, query classification, rewrite, hybrid search, rerank, SQL branch, **single-doc handlers (lookup / extraction / summarize / table-math)**, full-doc context mode, streamed generation, Langfuse traces, citation formatting.

**Owns:**

- `services/retrieval-api/**`
  - `services/retrieval-api/scope.py` — deterministic scope extractor (LLM-free; see ADR-009).
  - `services/retrieval-api/handlers/` — one handler per cell of the (scope × intent) dispatcher matrix.
  - `services/retrieval-api/dispatcher.py` — strict switch; no implicit fallthrough.
- `config/retrieval/prompts/**` (sub-folders per language per intent).
- `tests/e2e_query.py`
- `tests/regressions/**` (including `tests/regressions/single_doc/`).
- `scripts/eval.py` and the eval dataset format (with **bucket partitions** per Phase 8).

**Must not touch:**

- Anything the ingestion-agent owns (may only read through public APIs / DB views).

**Inputs:** Architecture §§6.2, 6.3, 7.3 (revised). ADRs 002, 004, 006, **009**. Phase 6 + Phase 8 of plan.

**Deliverables:**

- retrieval-api with SSE streaming, full pipeline.
- System prompt templates (DE, EN, bilingual) with clear citation rules.
- Model router (`pick_model(query_class) -> model_name`).
- `scripts/eval.py` that reads a YAML eval set and prints recall@3, recall@10, citation accuracy per class.
- Regression test harness that the coordinator can rerun unchanged.

**Acceptance:** A6.1–A6.5, A8.1–A8.3.

**Tools:** `Read`, `Write`, `Edit`, `Bash`, `Grep`, `Glob`.

**Hard rules:**

- Citation format is **fixed**: each cited fragment gets `[n]` where `n` is 1-based across the response, and the final SSE `citations` event lists them in order with `doc_id`, `chunk_id`, `page`, `score`, `rerank_score`, and a ≤ 240-char preview.
- Refusal style: if no chunk supports the answer, respond in the user's language: DE "Ich habe dazu in den zugänglichen Dokumenten keine Information gefunden." / EN "I didn't find information on that in the documents you can access."
- **Never** call an LLM without a Langfuse trace.
- **Never** bypass the ACL filter. There is exactly one Qdrant search code path; the ACL filter is built from `request.principal.groups` and is an assertion, not a config.
- Prompt templates are files on disk, versioned; runtime changes require editing the file and a restart — no hot admin UI for prompts in v1.
- **Scope extraction is deterministic and LLM-free.** No LLM call in `scope.py`. The LLM classifier's `scope_label` is *advisory* — the extractor wins when its confidence ≥ `SCOPE_MIN_CONFIDENCE`.
- **Single-doc extraction does not use rerank truncation.** The handler fetches all chunks of the target doc (ordered by `ord`) and either stuffs them (`≤ FULL_DOC_CONTEXT_THRESHOLD`) or map-reduces by section. Rerank is only in multi-doc paths.
- **Scope chip does not grant ACL.** A chip narrows the read set; it cannot broaden it. ACL remains independently enforced.

**Testing contract:** every system prompt change must include an updated `scripts/eval.py` baseline and show uplift or parity on the 50-query set.

**Escalation:** if `bge-reranker-v2-m3` latency exceeds SLO on reference hardware, propose alternatives (smaller reranker, batching) with numbers; do not silently remove reranking.

---

## 5. ui-agent

**Purpose:** configure Open WebUI, the custom pipeline, OIDC, citation rendering, theming, and admin views.

**Owns:**

- `config/openwebui/**`
- `config/pipelines/reineke_rag.py`
- `config/openwebui/theme/` (minimal)

**Must not touch:** anything in `services/**`.

**Inputs:** Architecture §7.6. Phase 7 of plan.

**Deliverables:**

- Fully configured Open WebUI with Authentik OIDC (authorisation code flow).
- Pipeline that forwards queries to retrieval-api and renders citations.
- Language toggle in UI (DE/EN) wired to a `lang` header forwarded to retrieval-api (used only for the system prompt selection).
- Basic theme: app name `Reineke-RAG`, primary colour from `owner-inputs.yaml` if given, else neutral.

**Acceptance:** A7.1–A7.4.

**Tools:** `Read`, `Write`, `Edit`, `Bash`, `WebFetch` (Open WebUI pipeline docs).

**Hard rules:**

- Only expose the `Reineke-RAG` model to end users (no raw Ollama model switcher).
- Open WebUI's own document upload feature is **disabled**; uploads go via admin UI or `rag-admin` CLI. (Rationale: uploads must carry folder + ACL metadata — a channel the default upload lacks.)
- No telemetry to Open WebUI maintainers: set `OPENWEBUI_ENABLE_TELEMETRY=false`.

**Escalation:** if Open WebUI's pipeline feature cannot pass the JWT cleanly, propose a thin reverse-proxy alternative before building around it.

---

## 6. llm-agent

**Purpose:** run Ollama, TEI reranker, model management, smoke tests, router heuristics, latency budgets.

**Owns:**

- `services/ollama-init/` (a one-shot container that runs `pull-models.sh`).
- `scripts/pull-models.sh`, `scripts/smoke-*.sh`.
- Model routing config consumed by retrieval-agent: `config/retrieval/models.yaml`.

**Must not touch:** retrieval-api code — only the model routing **config**.

**Inputs:** Architecture §§2 component 10-11, 8; Phase 3 of plan.

**Deliverables:**

- Ollama + TEI services in Compose (PR'd to deployment-agent).
- One-shot init container that pulls all models in parallel, skipping those already present.
- `models.yaml` structured as:
  ```yaml
  classes:
    lookup:     {model: gemma2:9b-instruct-q5_K_M, max_tokens: 400}
    extraction: {model: qwen2.5:32b-instruct-q4_K_M, max_tokens: 1200}
    table-math: {model: qwen2.5:32b-instruct-q4_K_M, max_tokens: 800}
    synthesis:  {model: llama3.3:70b-instruct-q4_K_M, max_tokens: 1600}
  embedding:   {model: bge-m3, dimensions: 1024}
  reranker:    {model: BAAI/bge-reranker-v2-m3, server: tei}
  ```
- Latency SLO assertions in smoke scripts.

**Acceptance:** A3.1–A3.4.

**Tools:** `Read`, `Write`, `Edit`, `Bash`.

**Hard rules:**

- Default `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_MAX_LOADED_MODELS=2` on 64 GB hosts to avoid thrash; overridable via env.
- TEI launched with `--max-concurrent-requests 4 --max-batch-tokens 8192`.
- Smoke scripts exit non-zero on latency or dimension mismatch.
- Never hardcode an OpenAI-compatible base URL in user-facing config — everything points to the internal Ollama service name.

**Escalation:** if a chosen model exceeds host memory in practice, propose a quantisation downgrade with a numbered table.

---

## 7. observability-agent

**Purpose:** Langfuse, Prometheus scrape, Grafana dashboards, Loki/Promtail, alert rules.

**Owns:**

- `config/langfuse/**`
- `config/grafana/provisioning/**`
- `config/prometheus/**`
- `config/loki/**`

**Must not touch:** service source code (only `/metrics` endpoint *contracts* — which it lists for other agents).

**Inputs:** Architecture §8; Phase 9 of plan.

**Deliverables:**

- Langfuse deployed; Admin UI accessible; API keys provisioned and wired into retrieval-api via env.
- Prometheus scraping all custom services + node + postgres-exporter + cadvisor.
- Three provisioned Grafana dashboards (Overview, Ingestion, Infra).
- A fourth Quality dashboard stub, wired but empty until Phase 8 data arrives.
- Alert rules: disk < 10 %, unhealthy > 5 min, ingestion queue > 200, backup missed > 26 h.

**Acceptance:** A9.2 plus dashboards load and show data.

**Tools:** `Read`, `Write`, `Edit`, `Bash`, `WebFetch`.

**Hard rules:**

- Langfuse runs **self-hosted**; never configure a cloud endpoint.
- Alerts go to a single channel (webhook URL from `owner-inputs.yaml`, default: log to file at `${DATA_ROOT}/alerts.log`).
- Every custom service must expose `rag_build_info{version,commit}` — observability-agent surfaces this in Overview dashboard.

**Escalation:** if a metric needed for a dashboard is not emitted by the owning service, file a list for the coordinator — do not edit other services' code.

---

## Interaction & handoff rules (all agents)

1. **Stay in lane.** Each agent only edits files it owns (listed above). If an edit elsewhere is needed, propose it as a diff comment in the phase handoff back to the coordinator.
2. **No hidden state.** Anything written to disk during a build must end up either in the repo or in `${DATA_ROOT}`. No `/tmp`-only state.
3. **Logs and tests are deliverables**, not extras. A phase is not "done" without tests + log updates.
4. **Ask, don't guess, on scope.** If the owner's intent is unclear for a choice *inside* your lane, escalate to coordinator → owner with ≤ 3 options.
5. **Every commit is small and named for the phase.** Conventional commits: `feat(ingestion): chunker respects XLSX sheet boundaries`. If the build uses git.
6. **No new external services.** Introducing a new container = ADR + coordinator approval.
7. **Offline contract.** Every image and model is pulled once; the runtime path must not require internet. Violations = automatic rejection.

## A concrete example: a failing phase

> *Phase 6 acceptance A6.2 fails: a `sales` user received a chunk from `/qms/` in their retrieval list, even though the answer was refused.*
>
> coordinator attaches the failing test log to the retrieval-agent with a prompt like:
>
> *"A6.2 failed. Log shows Qdrant returned 3 points from folder_path=/qms/normen for user with groups=[sales,guest]. The ACL filter must be **mandatory** on every search, including the rewrite paraphrases. Inspect retrieval/search.py and the query-rewrite branch. Do not broaden scope."*
>
> The retrieval-agent re-dispatches, fixes the oversight (the rewrite branch built a separate Qdrant call without copying the filter), updates tests, returns. Coordinator reruns A6.2 — green — advances to Phase 7.

This is the operational tempo the coordinator must enforce throughout.

---

## Sub-agent specialisations summary

| Phase(s) | Owner | One-line summary |
|----------|-------|------------------|
| 1, 9 | deployment-agent | Compose + ops plumbing |
| 2 | auth-agent | Authentik + JWT lib |
| 3 | llm-agent | Ollama + TEI + model routing |
| 4, 5 | ingestion-agent | Docling + pipeline + DuckDB |
| 6, 8 | retrieval-agent | Query → streamed cited answer |
| 7 | ui-agent | Open WebUI + pipeline + theming |
| 9 | observability-agent | Langfuse + Grafana + alerts |

The coordinator is always on duty.
