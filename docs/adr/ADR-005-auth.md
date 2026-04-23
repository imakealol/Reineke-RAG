# ADR-005 — Authentik as the identity provider

- **Status:** Accepted
- **Date:** 2026-04-23

## Context

We need one identity system that:

1. Issues **OIDC** tokens for multiple services (Open WebUI, Langfuse, Grafana, n8n, our three FastAPI services).
2. Manages **groups** that we use as ACL labels.
3. Provides a decent **self-service** flow (password change, MFA enrolment).
4. Runs **on-prem**, single Docker container or small set.
5. Has an ergonomic admin UI (we don't have a dedicated IAM team).

## Options considered

| Option | OIDC | Groups/claims | Admin UX | Resource use | Notes |
|--------|------|---------------|----------|--------------|-------|
| **Authentik** | ✔ | ✔ (native) | Modern, friendly | ~1 GB RAM + Postgres | Blueprint YAML for reproducible bootstrap |
| Keycloak | ✔ | ✔ | Mature, complex | ~2 GB RAM | Robust; higher learning curve |
| Dex + static | ✔ | limited | no UI | tiny | No user mgmt; would still need one of: LDAP / Gitea / etc. |
| ORY Kratos + Hydra | ✔ | ✔ | build-your-own UI | modular | Too much scaffolding |
| FreeIPA + Keycloak | ✔ | ✔ | heavy | heavy | Overkill |
| Rolling our own | ❌ | ❌ | ❌ | low | No. Never. |

## Decision

**Authentik** is deployed with its own Postgres + Redis. Its **blueprint** feature lets us declare the initial state (groups, OIDC apps, flows) in YAML committed to the repo — reproducible and reviewable.

- Algorithms: RS256, 2048-bit keys.
- Token lifetime: access 15 min, refresh 24 h.
- `groups` scope is added to every OIDC app so services receive the user's group list in their JWTs.
- Email verification OFF by default (closed internal setup); documented as a toggle.
- SMTP configuration optional; when absent, admin resets passwords manually.

## Consequences

Positive:

- One login for everything on the LAN.
- Group management is the only surface an admin learns.
- Blueprints mean disaster-recovery is "restore the DB, apply blueprints".

Negative:

- Authentik has been less common than Keycloak in older enterprises; training a new admin may include reading Authentik docs. Mitigated by `04_OPERATIONS.md` section 3 covering the day-to-day paths.
- Authentik's upgrade cadence is brisk; we pin a minor version and test upgrades on staging before production.

## Audit & logging interaction

- Authentik emits login and token events; we ship its logs to Loki along with everything else.
- Our own `rag.audit_log` is a separate stream focused on *query* events. Correlation via `user_id` (Authentik `sub`).

## Escape hatch

- If Authentik becomes unavailable mid-operation, `ADMIN_BACKUP_TOKEN` — a long-lived, emergency-only JWT — grants admin access to `rag-admin` and the APIs. Stored in the password manager, rotated every 90 days, never used for normal operation. Use is logged loudly.
