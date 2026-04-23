# Build Log

> Append-only record of every build event. Maintained by the coordinator agent. One entry per action (dispatch, acceptance, commit, escalation). Never rewrite history; errors become new entries.

Format:

```
## YYYY-MM-DD HH:MM — Phase N — <actor> — <outcome>

<one-line event>
<evidence pointer: command run, log snippet, commit hash>
```

---

## 2026-04-23 — Phase 0 — coordinator — planning

Repository seeded with concept, architecture, implementation plan, agent briefs, ADRs, skeleton configs. Awaiting owner inputs (`config/owner-inputs.yaml`) before Phase 1 kickoff.

Open items:
- Owner to confirm folder taxonomy (sample in `config/owner-inputs.yaml.example`).
- Owner to confirm backup target, primary domain, HTTPS strategy.
- Owner to decide `LLM_PROFILE` (`default` / `compact` / `heavy-70b`) — see ADR-004.
- Owner to supply 10–50 sample documents for Phase 4 fixtures; ≥ 100 docs + ≥ 100 bucketed gold queries for Phase 8 eval.
- **macOS specific**: owner to install native Ollama (`brew install ollama && brew services start ollama`) before Phase 1 — enforced by A0.9 and ADR-010.

Next: dispatch nothing yet. Gather inputs, then dispatch `deployment-agent` for Phase 1.

## 2026-04-23 — Design revision — planner — owner audit request

Owner requested an audit focused on **single-document question quality** and **execution feasibility** on the reference M4 Max.

Gaps found and closed by this revision (all design-time, no code regression):

- **Single-document queries were implicit** — no scope extraction, no full-doc context mode, no extraction-completeness path, no UI scope affordance. Addressed by new **ADR-009** and a rewrite of `docs/02_ARCHITECTURE.md` §7.3 (scope extractor, five-cell dispatcher, full-doc context at `≤ 20 480` tokens).
- **Ollama-in-Docker on Apple Silicon would be 10× too slow** (no Metal passthrough). Addressed by **ADR-010**: native Ollama on macOS is mandatory; Compose profile `ollama-docker` off by default on Mac. New acceptance A0.9 (Phase 0) and A3.5 (Phase 3) enforce this.
- **bge-m3 sparse pathway was vague** (Ollama doesn't expose sparse). Clarified in **ADR-003**: FlagEmbedding in-process, shared by ingestion-worker and retrieval-api.
- **Heavy tier on Mac was wishful.** `llama3.3:70b` now explicit opt-in via `LLM_PROFILE=heavy-70b`; default heavy tier on Mac is Qwen 2.5 32B. **ADR-004** sizing matrix updated.
- **Reranker on Apple Silicon** — TEI has no Metal backend. Added `services/reranker` (sentence-transformers MPS) as the macOS default; TEI remains the Linux-GPU default. Both expose the same HTTP contract.
- **Phase 6 acceptance** extended with **A6.6 – A6.10** covering filename-anchored extraction, full-doc summary faithfulness, scope chip propagation, page-anchored retrieval, follow-up inheritance.
- **Phase 8 eval** expanded to ≥ 100 queries, bucketed (single-doc lookup / extraction / summarize / table-math + multi-doc lookup / table-math + synthesis + ACL leak probes); per-bucket pass criteria (A8.1 revised).
- **retrieval-agent brief** updated to own scope.py, handlers/, dispatcher.py explicitly; hard rules added (LLM-free scope extractor; no rerank truncation in single-doc extraction; chip cannot widen ACL).
- **Docker Compose + .env.example** updated: `OLLAMA_MODE`, `RERANK_MODE`, `LLM_PROFILE`, `FULL_DOC_CONTEXT_THRESHOLD`, `SCOPE_MIN_CONFIDENCE`.

A coordinator kickoff prompt is provided at `COORDINATOR_PROMPT.md` for starting the build in a fresh Claude Code session.
