# 04 — Operations Handbook

> For the **administrator / IT operator** who runs Reineke-RAG. Installation, upgrades, backups, user management, monitoring, troubleshooting, performance tuning, offboarding. Assumes the concept + architecture docs have been read.

---

## 1. Prerequisites

### 1.1 Host

| Resource | Minimum | Reference | Recommended |
|----------|---------|-----------|-------------|
| CPU / SoC | 8 cores | Apple M4 Max (14 cores) | M4 Max or better, or Linux x86 + 24 GB GPU |
| RAM | 32 GB | 64 GB | 64–128 GB |
| Disk (SSD) | 250 GB | 1 TB | 1 TB + external backup |
| OS | macOS 14+ / Ubuntu 22.04+ | macOS 15 on M4 Max | — |
| Container runtime | Docker 24+ or Colima 0.7+ | Colima 0.7 | — |

On macOS, Colima with `--cpu 10 --memory 48 --disk 200` leaves headroom for the host. Docker Desktop works too; consume the same resource budget.

### 1.2 Network

- Internal DNS entry: `rag.<company>.local` → host IP.
- Reachable ports on host: **80** (HTTP → auto-redirects to HTTPS) and **443** (HTTPS). No other inbound ports required.
- **No outbound** required at runtime. *During initial setup* outbound is needed for pulling Docker images and model weights. Plan for a one-time "online bootstrap" window if the host is normally air-gapped.

### 1.3 Secrets provisioning

Before install, collect in a password manager:

- `AUTHENTIK_SECRET_KEY` (random 64 chars).
- `AUTHENTIK_BOOTSTRAP_PASSWORD` (for first admin).
- `POSTGRES_PASSWORD`, `QDRANT_API_KEY`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`.
- `LANGFUSE_SALT`, `LANGFUSE_NEXTAUTH_SECRET`.
- `INTERNAL_SERVICE_TOKEN` (shared secret between the custom FastAPIs).

`scripts/bootstrap.sh` generates these if absent and writes `.env`. **Store `.env` in your password manager too.**

---

## 2. Installation

### 2.1 First-time install

```sh
# 1. Clone or extract the release
cd /opt && tar xf reineke-rag-v1.0.0.tar.gz && cd reineke-rag

# 2. Generate .env (secrets) and inputs
bash scripts/bootstrap.sh
# - prompts for PRIMARY_DOMAIN, BACKUP_ROOT, DATA_ROOT
# - writes .env and config/owner-inputs.yaml

# 3. Pull images (online)
make pull

# 4. Pull LLM & embedding models (online, slow; ~80 GB total)
make pull-models                        # or PULL_HEAVY=false make pull-models

# 5. Start
make up

# 6. Wait for healthchecks
make wait-healthy                       # polls; 2-5 min on first start

# 7. Finish Authentik bootstrap
open https://$PRIMARY_DOMAIN/auth/
# Log in with bootstrap admin / temporary password (printed by bootstrap.sh)
# Change password, set up TOTP
# Confirm groups and OIDC apps exist (blueprints auto-applied)

# 8. Create folders + ACLs
rag-admin folders sync config/owner-inputs.yaml

# 9. Smoke test
rag-admin query "Ping"                  # expects refusal; proves auth + retrieval path
```

### 2.2 Air-gapped install

- On an online helper machine: `scripts/pack-offline.sh` produces a ~100 GB bundle containing:
  - Saved Docker images (`docker save`),
  - Pre-pulled Ollama models,
  - Docling OCR models,
  - TEI reranker weights,
  - npm/pip wheels used by custom services.
- Transfer to target. On target: `scripts/load-offline.sh` untars and `docker load`s everything, then `make up` as normal.

### 2.3 Day-2 CLI

The `rag-admin` CLI is a thin wrapper that authenticates as admin and calls the APIs. Most frequent commands:

```sh
rag-admin status                         # container health + queue depth + doc count
rag-admin users list
rag-admin users add alice@reineke.de --groups engineering,qms
rag-admin folders list
rag-admin folders set /qms/normen qms,engineering,admin
rag-admin docs list --folder /qms/normen
rag-admin docs upload ./drop/*.pdf --folder /qms/normen
rag-admin docs reindex <doc_id>
rag-admin docs delete <doc_id> [--hard]
rag-admin query "Welche Norm ..."        # server-sent answer to stdout, for tests
rag-admin models list
rag-admin jobs list --state failed
rag-admin backup run                     # trigger a manual backup now
rag-admin restore plan <backup-dir>      # dry-run
```

---

## 3. User & group management

Users and groups live in **Authentik**. The app DB only mirrors the minimum (email, groups) for audit joins and refreshes it on each JWT validation.

### 3.1 Add a user

1. Log into `https://$PRIMARY_DOMAIN/auth/` as admin.
2. Directory → Users → Create.
3. Fill name, email. Leave password empty → invite email if SMTP configured, else use "Set password" to deliver by another channel.
4. Directory → Groups → pick group(s) → add user as member.
5. Users log in at `https://$PRIMARY_DOMAIN/` next time and will land in the chat UI with their new permissions.

### 3.2 Add a group

1. Directory → Groups → Create (name = the ACL label).
2. Update folders that should grant access: `rag-admin folders set /<path> group1,group2,…`.
3. ACL propagation is asynchronous; watch `rag-admin jobs list --kind reacl` until it drains.

### 3.3 Disable / offboard

1. In Authentik, disable the user (toggle "Active").
2. Refresh tokens expire within 24 h; access tokens within 15 min. A disabled user stops working by the next minute in practice.
3. Chats/audit entries are retained per policy; anonymisation procedure in `docs/privacy.md` (draft).

### 3.4 Password reset

Self-service via Authentik's built-in flow (email link) if SMTP is configured; otherwise admin sets a temporary password via Directory → Users → Reset.

---

## 4. Folders & ACLs

The folder tree is **logical**, not filesystem. `rag-admin folders` is the single source of truth.

```sh
rag-admin folders create /qms/normen --groups qms,engineering,admin --description "QMS: Normen und Vorschriften"
rag-admin folders set    /qms/normen qms,engineering,admin,auditor   # change groups
rag-admin folders move   /qms/normen /qms/standards                  # rename (requires reindex)
rag-admin folders delete /qms/normen --wait                          # fails if docs exist
```

- **ACL change propagation:** Qdrant payload rewrite, no re-embedding. Typically < 30 s per 1 000 chunks.
- **Folder rename:** rewrites `folder_path` on every document row. Does not trigger re-embedding.

---

## 5. Document operations

### 5.1 Bulk ingestion

```sh
# Put files in a folder structure matching the logical tree
mkdir -p drop/qms/normen
cp /Volumes/Share/QMS/*.pdf drop/qms/normen/

# Ingest recursively
rag-admin docs ingest-dir drop/  --base-folder /
# or dry-run:
rag-admin docs ingest-dir drop/ --base-folder / --dry-run
```

Progress is visible in Grafana → *Ingestion* dashboard. Failed files stay in `queued` with error reasons; `rag-admin jobs list --state failed` prints them.

### 5.2 Re-indexing

Needed when:

- Chunking config changed (`CHUNK_MAX_TOKENS` etc.).
- A parser upgrade is expected to improve quality.
- Source file was updated in place (rare — prefer versioning).

```sh
rag-admin docs reindex <doc_id>                       # one
rag-admin docs reindex --folder /qms/normen            # all in folder
rag-admin docs reindex --all --confirm                 # nuclear
```

### 5.3 Deletion

- Soft delete: `rag-admin docs delete <doc_id>`. File marked `superseded`, removed from index. Kept 30 days.
- Hard delete: `rag-admin docs delete <doc_id> --hard`. Irreversible. Requires `ADMIN_CONFIRM=yes` env var to nudge you to think about backups first.

### 5.4 Document versioning

Uploading a same-name file to the same folder creates a new document ID if SHA-256 differs; the old one is automatically marked `superseded`. The index drops old chunks; the old bytes remain in MinIO (`raw/{old_id}/...`) for 30 days.

---

## 6. Backups & restore

### 6.1 What gets backed up

See `02_ARCHITECTURE.md` §11. Summary:

- `postgres`, `authentik-db`, `langfuse-db` via `pg_dump -Fc`.
- `minio` via `mc mirror`.
- `qdrant` via snapshot API.
- `duckdb` by file copy.
- `redis` AOF.

### 6.2 Schedule

- Nightly at **02:15 local**.
- Retention: **7 daily / 4 weekly / 12 monthly** (GFS rotation).
- Output: `${BACKUP_ROOT}/YYYY-MM-DD/`.

Launchd (macOS) unit installed by `scripts/install-schedule.sh`. On Linux use the shipped systemd timer.

### 6.3 Manual backup

```sh
rag-admin backup run                  # same script as nightly
ls -lh $BACKUP_ROOT/$(date +%F)/      # verify output
```

### 6.4 Restore rehearsal (must be done at least once)

```sh
make down
sudo mv /var/lib/reineke /var/lib/reineke.old
sudo mkdir /var/lib/reineke && sudo chown $USER /var/lib/reineke

rag-admin restore plan $BACKUP_ROOT/2026-04-22/
rag-admin restore apply $BACKUP_ROOT/2026-04-22/
make up
make wait-healthy
rag-admin query "Welche Norm gilt für Typ-B-Schränke?"
# Expect previously-indexed content to return with citations.
```

If the rehearsal fails, **fix it now** — not the night of an incident.

### 6.5 Encryption at rest

- macOS: FileVault on the data disk is sufficient.
- Linux: LUKS for the `DATA_ROOT` partition, or ZFS native encryption.
- Backups: tarballs encrypted with `gpg -c` against a 25-char random passphrase, passphrase in password manager. `scripts/backup.sh` handles it when `BACKUP_GPG_PASSPHRASE_FILE` is set.

---

## 7. Monitoring

### 7.1 Dashboards (Grafana)

- `/grafana/d/overview` — QPS, p95 latency by class, error rate, model usage.
- `/grafana/d/ingestion` — queue, throughput, failures per mime type.
- `/grafana/d/infra` — container CPU/RAM, disk free, network.
- `/grafana/d/quality` — populated from Langfuse exports; rerank uplift, refusal rate, top "no citation" queries.

### 7.2 Langfuse (LLM traces)

`https://$PRIMARY_DOMAIN/langfuse/`. The most useful debugging tool:

- Filter by latency > 10 s to find slow queries.
- Filter by `retrieval.rerank_score_top < 0.4` to find low-confidence retrievals.
- Re-run a stored prompt verbatim with a different model from the UI.

### 7.3 Alerts

Default alert channels: log to `${DATA_ROOT}/alerts.log`; optional webhook (Teams / Slack) via `ALERT_WEBHOOK_URL`.

Default rules:

- `rag_disk_free_pct < 10` (critical).
- Any container `unhealthy` for > 5 min.
- `rag_ingestion_queue_depth > 200` (warning, 500 critical).
- Backup not run in 26 h (critical).
- p95 latency on `lookup` class > 8 s for 10 min (warning).

Silence a running alert: `rag-admin alerts silence <rule> --until 2026-05-01`.

---

## 8. Performance tuning

### 8.1 Model routing (retrieval quality ↔ latency trade)

Edit `config/retrieval/models.yaml`. Typical knobs:

- Downgrade `synthesis` from 70 B to 32 B if p95 > 60 s on a M-series box. You lose some synthesis quality; measure with the eval set.
- Swap `lookup` model to `llama3.1:8b-instruct-q6_K` if German answers are weak.

Restart: `docker compose restart retrieval-api`. No reindex needed.

### 8.2 Chunking (recall ↔ precision trade)

Edit `.env`: `CHUNK_MAX_TOKENS` default 512. Raising to 800 can improve recall on narrative docs; lowering to 300 often helps precise lookups. **Requires reindex.**

### 8.3 Retrieval k (recall ↔ rerank cost)

`TOP_K_DENSE` / `TOP_K_SPARSE` (default 50 each) into a `TOP_K_RERANK` (default 12). Raising dense/sparse helps recall at the cost of rerank latency (linear in candidates). No reindex.

### 8.4 Ollama concurrency

- `OLLAMA_NUM_PARALLEL=1` on Apple Silicon (default, avoids Metal contention).
- `OLLAMA_MAX_LOADED_MODELS=2` default; keep at 2 on 64 GB. Raising to 3 risks swap on heavyweight queries.

### 8.5 Qdrant

- `on_disk_payload=true` (default) minimizes RAM.
- Enable `quantization.scalar.type=int8` (default) — negligible quality loss, 4× less RAM per vector.
- For > 5 M points, consider `hnsw.ef=128` at query time for better recall.

### 8.6 Disk

- `docker system df` and prune weekly — `docker system prune -f --filter until=168h`.
- Ollama model cache grows; if switching models, `ollama rm <old>` frees GB.

---

## 9. Upgrades

### 9.1 Patch / minor (`v1.0.0` → `v1.0.1`)

```sh
git fetch && git checkout v1.0.1
make pull
make up
make wait-healthy
```

Changelog entries are read before the upgrade; look for **"migration"** or **"reindex"** notes.

### 9.2 Major (`v1.x.y` → `v2.0.0`)

- Read the **migration notes** in the release.
- Likely triggers: new embedder, new chunker, vector shape change.
- Plan a maintenance window (30–120 min depending on corpus size).
- Backup first. Then:

```sh
make backup
git checkout v2.0.0
make pull
rag-admin migrate preflight     # lists required reindex scope
rag-admin migrate apply         # queues reindex; progress in Grafana
```

### 9.3 Ollama model upgrades

- Pull new: `ollama pull qwen2.5:32b-instruct-q5_K_M`.
- Update `config/retrieval/models.yaml` and restart `retrieval-api`.
- Run `scripts/eval.py` to compare baselines; keep the better one.

---

## 10. Security operations

### 10.1 Credential rotation

- `scripts/rotate-secrets.sh` rotates: Postgres passwords, Qdrant API key, MinIO keys, internal service token.
- Takes < 1 min; services restart rolling.
- Authentik admin account: rotate via UI; set a new recovery secret in the password manager.

### 10.2 Updating TLS certs

Caddy auto-rotates its internal CA certs. If distributing the CA cert to clients, update on rotation (every 90 days). Script: `scripts/export-ca.sh > ca.crt`.

### 10.3 Incident response checklist

- **Suspected leak of credentials** → rotate secrets, invalidate all active tokens (`rag-admin sessions revoke-all`), audit last 30 days of `audit_log` for unusual queries.
- **Host compromise suspected** → `make down`, image the disk, restore from the last clean backup onto a fresh host, rotate everything.
- **User reports wrong/leaking answer with PII** → snapshot the audit row and the offending Qdrant chunk; review ACL of the source document; consider a scoped delete.

### 10.4 Audit log export

`rag-admin audit export --from 2026-01-01 --to 2026-03-31 --format csv > q1.csv`.
Fields documented in `02_ARCHITECTURE.md` §4.

---

## 11. Troubleshooting — admin edition

### 11.1 Container down

```sh
docker compose ps
docker compose logs --since 30m <service>
docker compose up -d <service>       # idempotent
```

### 11.2 "All queries refused"

- Check `retrieval-api` logs for `ACL filter: groups=[]`. Probably JWT is missing `groups` claim.
- Re-check the Authentik OIDC app's property mappings. The `groups` scope must be enabled.

### 11.3 "Ingestion stuck"

- `docker compose logs ingestion-worker` — likely Docling exception.
- `rag-admin jobs list --state failed`; re-enqueue after fix with `rag-admin jobs retry <job_id>`.
- Common cause: password-protected PDF. The error message says so; owner must provide an unlocked copy or admin enables the optional `PDF_UNLOCK_ATTEMPTS=true` flag.

### 11.4 "Answers feel wrong"

- Open Langfuse, filter by user or by time. Open a bad trace; compare the reranker's top 5 with the gold chunk (if known). If the gold chunk is nowhere in the top-50, it's a **recall** problem (re-chunk, adjust `TOP_K_DENSE`); if it's there but unranked, it's a **rerank** problem (look for mismatched language, or consider swapping reranker).

### 11.5 "Everything is slow"

- Check `ollama ps` — which models are loaded? If two heavyweight models are loaded, they're sharing GPU/Metal bandwidth. Lower `OLLAMA_MAX_LOADED_MODELS`.
- Check host `top` — CPU pinned? Colima resource limits ok?
- Check disk I/O — Grafana "Infra". High iowait usually points to Ollama model swapping or Docker logs.

### 11.6 Qdrant collection corruption

Rare, but: `scripts/qdrant-snapshot.sh apply <snapshot>` restores. If no snapshot (you never ran `make backup`), reindex is the fallback — slow but complete: `rag-admin docs reindex --all --confirm`.

---

## 12. Decommissioning / scaling down

1. `make down`.
2. `rag-admin backup run` (final).
3. Archive `${BACKUP_ROOT}` to cold storage.
4. Remove `${DATA_ROOT}` after confirming archive integrity.
5. Revoke Authentik applications' secrets.
6. Remove DNS entry.

---

## 13. Appendix A — Port map (inside Docker network)

| Service | Port | Notes |
|---------|------|-------|
| caddy | 80, 443 (host) | Only host-bound ports |
| authentik-server | 9000 | UI + OIDC |
| postgres | 5432 | rag DB |
| redis | 6379 | queue + pub/sub |
| minio | 9000 (S3), 9001 (console) | |
| qdrant | 6333 (REST), 6334 (gRPC) | API-key auth |
| ollama | 11434 | no auth |
| tei-reranker | 8080 | internal |
| docling | 8001 | |
| ingestion-api | 8010 | JWT |
| retrieval-api | 8020 | JWT |
| duckdb-api | 8030 | JWT |
| openwebui | 8080 (internal) | proxied |
| pipelines | 9099 | internal |
| langfuse | 3000 | |
| prometheus | 9090 | |
| grafana | 3000 | proxied under /grafana |
| loki | 3100 | |

## 14. Appendix B — Capacity planning (rule-of-thumb)

| Corpus | Qdrant disk | Qdrant RAM (int8) | MinIO disk | Postgres |
|--------|-------------|-------------------|------------|----------|
| 500 docs (~50 k chunks) | ~0.5 GB | ~0.2 GB | original × 1.05 | ~0.5 GB |
| 2 000 docs (~200 k chunks) | ~2 GB | ~0.8 GB | ditto | ~1.5 GB |
| 10 000 docs (~1 M chunks) | ~10 GB | ~4 GB | ditto | ~8 GB |
| 50 000 docs (~5 M chunks) | ~50 GB | ~20 GB | ditto | ~40 GB |

Ollama models are a fixed ~80 GB ceiling (all three LLMs + embedder + reranker). Leave 20 % disk headroom.

## 15. Appendix C — Useful one-liners

```sh
# Count indexed chunks
docker compose exec postgres psql -U rag -c "select count(*) from rag.chunks;"

# Top slow queries, last 24 h
docker compose exec postgres psql -U rag -c "
  select substring(query,1,80), latency_ms
  from rag.audit_log
  where ts > now() - interval '24 hours'
  order by latency_ms desc limit 10;"

# Qdrant collection sanity
curl -s -H "api-key: $QDRANT_API_KEY" http://localhost:6333/collections/chunks | jq .

# Force a reparse of a single doc
rag-admin docs reindex <doc_id>

# Who is online (active sessions in last 5 min)
docker compose exec postgres psql -U rag -c "
  select count(distinct user_id) from rag.audit_log
  where ts > now() - interval '5 minutes';"
```
