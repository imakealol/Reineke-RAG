# CLAUDE.md — project context for Claude Code

> This file is automatically loaded into every Claude Code session started in this repo. It orients the AI about **what this project is** and **how to work on it**.

## What this project is

**Reineke-RAG** — a fully offline, enterprise-grade Retrieval-Augmented Generation stack for internal Word / PDF / Excel documents. German + English corpus. Runs on Apple Silicon (M4 Max, 64 GB) or Linux + GPU. Built to deliver **reliable, cited, ACL-aware answers** that the n8n self-hosted AI starter kit can't.

The repository currently contains **concept and documentation only**. The actual stack is built by a **coordinator agent** that delegates to specialist subagents (see `docs/06_AGENT_BRIEFS.md`).

## Required reading (in order)

1. `README.md` — what + why + document map.
2. `docs/01_CONCEPT.md` — north-star concept, requirements, tech decisions.
3. `docs/02_ARCHITECTURE.md` — components, data flows, schemas.
4. `docs/05_IMPLEMENTATION_PLAN.md` — phased build plan with acceptance criteria.
5. `docs/06_AGENT_BRIEFS.md` — spec for every subagent.
6. `docs/adr/` — the eight Architecture Decision Records.

End-user and admin docs (`03_HANDBOOK.md`, `04_OPERATIONS.md`) describe the **finished** system; read them to understand target behaviour, not as build instructions.

## House rules for agents working here

1. **Concept & ADRs are frozen.** Changes require owner approval and a new ADR superseding the old.
2. **Stay in your lane.** Every subagent has an "Owns" list in `06_AGENT_BRIEFS.md`. Don't edit files outside it.
3. **Offline contract.** The stack must not make runtime outbound calls. External dependencies are pulled once; code that calls external SaaS is rejected.
4. **Every answer cites.** The retrieval service's system prompt enforces this; do not add a "creative mode".
5. **Langfuse traces every LLM call.** No LLM invocation without a span.
6. **ACL filter is mandatory.** Every Qdrant / DuckDB path runs it; any code path without it is a bug.
7. **Prefer boring tech** (Postgres, Docker, OIDC) for infrastructure.
8. **No framework adoption**: LangChain / LlamaIndex / Haystack are *components*, not the foundation. See ADR-008.

## Build tempo

The coordinator runs phases in order (0 → 10). Each phase has acceptance criteria; the coordinator verifies them itself before advancing. Failures re-dispatch the owning subagent with evidence. See `docs/05_IMPLEMENTATION_PLAN.md`.

## Working files an agent may create

- `BUILD_LOG.md` at repo root — append-only record of every phase's events.
- `config/owner-inputs.yaml` — real values, generated from the example by `scripts/bootstrap.sh`.
- `services/**` — new code per subagent ownership.
- `.env` — generated, never committed.

## Do not

- Start implementing before `config/owner-inputs.yaml` exists.
- Pull models without the operator's consent (heavy on bandwidth and disk).
- Expose any port besides 80/443 on the host.
- Use `:latest` image tags in `docker-compose.yml`.
- Add a "temporary" external API call "just to test."
