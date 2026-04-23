# Handover — Reineke-RAG v1.0.0 (template)

> Populated by the coordinator at Phase 10 and countersigned by the owner.
> Until then, this file is intentionally sparse.

## Status at handover

- Version: _to be set at tag time_
- Built on: _hostname, OS, hardware_
- Built for: Reineke Technik
- Countersigned by owner (date, name): _________________

## Scope delivered

- Ingestion: PDF / DOCX / XLSX incl. scanned PDFs (OCR).
- Retrieval: hybrid (dense + sparse) + rerank + ACL enforcement.
- Answers: cited, bilingual (DE + EN), with 4-class routing (lookup / extraction / table-math / synthesis).
- UI: Open WebUI with OIDC login, citations panel.
- Admin: `rag-admin` CLI, Grafana dashboards, backup/restore runbook.

## Scope explicitly NOT delivered (v1)

See `docs/01_CONCEPT.md` §10.

## Operational quick-reference

- Primary URL: `https://${PRIMARY_DOMAIN}/`
- Admin URL: `https://${PRIMARY_DOMAIN}/auth/` (Authentik)
- Observability: `https://${PRIMARY_DOMAIN}/grafana`, `/langfuse`
- Secrets vault: _(owner's password manager reference)_
- Backup target: `${BACKUP_ROOT}`
- Runbook: `docs/04_OPERATIONS.md`

## Known limitations & workarounds

(filled at handover from acceptance reports and `docs/eval/baseline-*.md`)

## Next steps after v1

- See `docs/01_CONCEPT.md` §10 + `docs/02_ARCHITECTURE.md` §12 (extensibility points).
- Highest-value candidates:
  1. Two-host split for heavy-model offload (when corpus > 5k docs + team > 30 users).
  2. Wiki / mail connectors.
  3. Per-document confidentiality tagging.
  4. Read/write ACL split (v1 lumps both under "access").

## Sign-off

| Role | Name | Date | Signature |
|------|------|------|-----------|
| Owner (operator) |   |   |   |
| Coordinator (AI) | coordinator@reineke-rag | YYYY-MM-DD | (auto) |

Completion of acceptance criteria from `docs/05_IMPLEMENTATION_PLAN.md` is the gate for this sign-off.
