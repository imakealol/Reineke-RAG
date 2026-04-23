# ADR-007 — Open WebUI with a custom pipeline

- **Status:** Accepted
- **Date:** 2026-04-23

## Context

We need a polished, multi-user chat UI that:

1. Supports OIDC login (Authentik).
2. Can route chat via **our** backend (retrieval-api), not straight to Ollama.
3. Renders **streamed answers + citations**.
4. Handles conversation history per user.
5. Feels familiar to users who have seen "ChatGPT-like" UIs.
6. Is self-hosted and actively maintained.

## Options considered

| Option | OIDC | Pipelines/routing | Citations | Maintenance | License |
|--------|------|--------------------|-----------|-------------|---------|
| **Open WebUI** | ✔ | ✔ "Pipelines" feature | ✔ (markdown + our pipeline) | very active, weekly | MIT |
| LibreChat | ✔ | limited | partial | active | MIT |
| Lobe Chat | ✔ (some flows) | via plugin | no native | active | MIT/commercial mix |
| AnythingLLM | ✔ | ties you into their RAG engine | ✔ | active | MIT |
| Custom React app | n/a | whatever we build | whatever we build | us | — |

## Decision

**Open WebUI**, deployed alongside the official `pipelines` container. Our custom pipeline (`reineke_rag.py`) intercepts chat completions and forwards them to `retrieval-api`, passing the user's JWT. Open WebUI is configured to expose **only** a "Reineke-RAG" model entry (not raw Ollama models) so users never accidentally ask an unconstrained LLM about internal topics.

Open WebUI's **own document upload** feature is **disabled** — uploads in Reineke-RAG must carry folder + ACL metadata that Open WebUI's upload does not support. Uploads go via the admin UI / `rag-admin` CLI.

## Consequences

Positive:

- Mature, polished UX on day one; minimal custom UI code.
- Conversation history, favourites, language toggle come for free.
- Pipelines feature is exactly the extension point we need.

Negative:

- A UI-centric project means we ride its upgrade cadence. Mitigation: pin minor version, test monthly with our pipeline before upgrading.
- The "model picker" metaphor is subtly misleading — we solve it by showing only one model; if Open WebUI changes the UX there, we monitor the setting.
- Multi-line citation rendering is implemented inside the pipeline (markdown). If Open WebUI adds a first-class "citations" field later, we migrate to it.

## UX non-goals (v1)

- No built-in document-explorer view. Admins use `rag-admin docs list` or the REST API.
- No per-chat model override. The router picks.
- No "agents" feature. Intentional focus on correct retrieval.

## Risks

- Open WebUI outgrowing our pipelines pattern → we pin versions and monitor release notes.
- Users demanding features we intentionally turned off (e.g. file attachments in chat) → documented in user handbook with rationale (ACL integrity).
