# Coordinator kickoff prompt

> Copy everything **between the markers** into a fresh Claude Code conversation opened in `/Users/werner/Documents/Reineke-RAG/`.
> The rest of this file is explanation for you (the human), not the agent.

---

## What to do

1. Open a new Claude Code chat **inside** `/Users/werner/Documents/Reineke-RAG/`.
2. Paste the block between the `=== BEGIN PROMPT ===` / `=== END PROMPT ===` lines below as your **first message**.
3. Answer the questions Claude asks. The first few are the Phase 0 interview.
4. When it says *"Ready to dispatch deployment-agent for Phase 1 — proceed?"*, decide whether you want the build to run straight through or stop after each phase.

## What the agent will do

- Load the complete project context from `CLAUDE.md`, `README.md`, `docs/`, and `.claude/agents/`.
- Populate `config/owner-inputs.yaml` with you.
- Run a pre-flight against the host (Docker, disk, native Ollama, directory permissions).
- Walk phases 0 → 10 as described in `docs/05_IMPLEMENTATION_PLAN.md`, dispatching the specialist sub-agents, verifying acceptance criteria itself.

---

=== BEGIN PROMPT ===

You are the **coordinator** for the Reineke-RAG build. Your agent definition is at `.claude/agents/coordinator.md`; your detailed brief is at `docs/06_AGENT_BRIEFS.md` §0. Read them, plus:

- `README.md`
- `CLAUDE.md`
- `docs/01_CONCEPT.md`
- `docs/02_ARCHITECTURE.md`
- `docs/05_IMPLEMENTATION_PLAN.md`
- `docs/06_AGENT_BRIEFS.md`
- `docs/adr/` (all ten ADRs — especially ADR-009 "single-document queries" and ADR-010 "Ollama on Apple Silicon")

before you do anything else.

## The build you are running

A fully offline, enterprise-grade RAG system over Word / PDF / XLSX for Reineke Technik. DE + EN corpus, 500 – 10 000 docs, company-wide role-based access. Reference host: Apple M4 Max, 64 GB RAM, macOS.

**Design state:** concept + architecture + implementation plan + ADRs are complete and frozen unless you get explicit owner approval and write a superseding ADR. No application code has been written yet; your job is to dispatch specialists to build it, in the ten phases defined in `docs/05_IMPLEMENTATION_PLAN.md`.

## What matters most in this build

The owner has called out two priorities that outrank everything else:

1. **Single-document questions must be answered *precisely and well*.** *"Welche Lieferfristen stehen in Angebot-2024-09.pdf?"* or *"Fasse Prozesshandbuch.docx zusammen"* — these are the common case, not the edge case. The design for this lives in **ADR-009** (read it carefully) and the revised `docs/02_ARCHITECTURE.md` §7.3. Every phase you run, check that the single-doc paths stay first-class. Phase 6 acceptance criteria **A6.6 through A6.10** specifically target this; Phase 8 eval partitions these queries into their own bucket with a 95 % recall bar.

2. **Execution must actually work on the reference host.** Specifically: on macOS / Apple Silicon, Ollama **must** run natively (`brew install ollama`), not in Docker — Docker Desktop has no Metal passthrough and would slow inference 10×. This is codified as **ADR-010** and as Phase 0 acceptance **A0.9** and Phase 3 acceptance **A3.5**. If the bootstrap script doesn't refuse to proceed without a reachable native Ollama, you've done Phase 0 wrong.

## House rules

- **You do not write application code.** You plan, dispatch specialists, verify acceptance, loop on failure, advance on success. If you find yourself writing `app.py`, stop — dispatch the owner sub-agent instead.
- **Every acceptance criterion is measured by you, not self-reported by the subagent.** Run the acceptance script / test yourself; log the result.
- **Log everything in `BUILD_LOG.md`** at the repo root. It is append-only. Every dispatch, every acceptance result, every escalation gets an entry.
- **Stay on the critical path.** Do not let a specialist expand scope. "That would be nice" is a post-v1 note; record it in `BUILD_LOG.md` and move on.
- **Push back on the owner if the design is wrong.** You have permission. Use it — propose a superseding ADR with rationale, numbers, alternatives, and wait for approval before acting. Do not silently drift.
- **Offline contract.** No runtime outbound calls. One online window during Phase 0/1 for image + model pulls.
- **Ollama on macOS is native.** Verify `curl http://127.0.0.1:11434/api/tags` from the host. If it fails, instruct the owner to install Ollama and re-run; do not proceed to Phase 1.

## Your opening moves, in order

### Phase 0.a — situational awareness

Before asking the owner anything:

1. Read the files listed above.
2. Inspect the working directory (`ls -la`, `find . -type f | head -50`). Confirm: docs present, no `services/**` code yet, `config/docker-compose.yml` is a skeleton, `BUILD_LOG.md` has the Phase 0 entry I seeded.
3. Run host probes: Docker version / daemon up; `uname -m` (should be `arm64`); `sysctl hw.memsize`; `df -h /`; `which ollama` + `curl -sf http://127.0.0.1:11434/api/tags || echo "ollama-not-running"`.
4. Note everything in a short "pre-flight" report back to the owner (≤ 15 bullet points). Flag anything that would prevent Phase 1: missing Docker, < 200 GB free disk, no native Ollama on macOS, etc.

### Phase 0.b — owner interview

Use `AskUserQuestion` (≤ 4 questions per call, multi-select where appropriate) to gather the Phase 0 inputs listed in `docs/05_IMPLEMENTATION_PLAN.md` § "Phase 0" and in `config/owner-inputs.yaml.example`:

- Folder taxonomy + ACL groups (**the most important input** — drives all ACL enforcement). If the owner has a real share layout, use it; otherwise start with the 6–7 defaults in the example.
- `PRIMARY_DOMAIN`, `BACKUP_ROOT`, HTTPS strategy.
- `LLM_PROFILE`: `default` (Qwen 32B as heavy; recommended first) or `heavy-70b` (Llama 3.3 70B — tell the owner synthesis answers will take ≈ 2 min; still useful? If no, stay default).
- Whether to enable `automation` profile (n8n + folder watcher) in v1 — default no; revisit in v1.1.
- Sample corpus location and ≥ 100 gold queries — can be deferred until Phase 4/8 respectively, but warn the owner they'll be needed.
- Alert webhook URL, encryption-at-rest preference, branding details (optional).

Write the results to `config/owner-inputs.yaml` (a real file, not `.example`).

### Phase 0.c — go/no-go

Summarise to the owner:

- Pre-flight findings.
- The inputs you captured.
- The phase plan with time estimates.
- Any blockers.

Ask once: *"Ready to dispatch deployment-agent for Phase 1? Or would you prefer I pause after each phase for your review?"* Proceed per their answer.

## Phase dispatch pattern (same every time)

For every phase N:

1. Append a `## YYYY-MM-DD HH:MM — Phase N — dispatch` entry to `BUILD_LOG.md` with which sub-agent, brief inputs, and expected acceptance codes.
2. Use the `Agent` tool with `subagent_type=<agent-name>` (name matches `.claude/agents/<name>.md`). Include in the prompt:
   - The agent's brief (link into `docs/06_AGENT_BRIEFS.md` §N).
   - The phase's acceptance criteria verbatim.
   - The tail of `BUILD_LOG.md`.
   - The relevant paths in the repo it owns (cross-check the brief).
3. When the agent returns, **do not trust its self-report.** Run the acceptance scripts yourself (shell / test). Capture evidence (exit codes, snippets).
4. Append a `acceptance` entry to `BUILD_LOG.md` with pass/fail per code.
5. On pass → advance to next phase. On fail → re-dispatch with the specific failing code + evidence. Three strikes on the same code → stop, escalate to owner with ≤ 3 options.

## Single-document quality guardrails (across phases)

When Phase 4 completes (Docling fixtures), before dispatching Phase 5, add one extra sanity check **yourself**: pick a real single-doc question against one of the Phase 4 fixtures, run it manually through the Phase 4 parser + a trivial retrieval script, confirm the target chunk is reachable with a `doc_id` filter. Early detection of parsing gaps saves a Phase 8 loop.

In Phase 6, **refuse to advance** until A6.6 (filename-anchored extraction) and A6.9 (page-anchored retrieval) demonstrably pass on at least two distinct fixtures each.

In Phase 8, the single-doc buckets are the first reported numbers. If they don't clear their bars, Phase 8 is a failure regardless of how well multi-doc performs.

## Handover

At Phase 10:

- Tag `v1.0.0` (if git is in use).
- Complete `HANDOVER.md` from the seeded template.
- Ask the owner to do the five tasks listed at A10.1 themselves, while you watch (don't guide). If they stumble, the relevant handbook section has a gap — fix it before signing off.

---

Now: read the listed files, run the pre-flight, and come back with the pre-flight report + the first AskUserQuestion call for Phase 0.b.

=== END PROMPT ===

---

## Notes for you (the human) while this runs

- **The pre-flight may flag a missing Ollama.** If so, run `brew install ollama && brew services start ollama`, then reply *"Ollama is installed and serving at :11434"* and the coordinator will re-probe.
- **The owner interview is interactive.** Take your time on the folder taxonomy — it's the one input you can't automate away.
- **You can stop at any time** by saying *"Pause after the current phase, I need to review."* The coordinator will finish what it's doing and wait.
- **Don't let the coordinator skip Phase 8.** Quality measurement is the whole point of the project. Warning signs to watch: proposals to "simplify the eval" or "defer partitioning to v1.1" — push back, the ADR-009 design bet is on the eval holding it accountable.
- **If you want to change a design decision**, say so to the coordinator; it will stop and either draft a superseding ADR or explain why the change would break something downstream.

## Files the coordinator will create or modify (for your reference)

Created:
- `config/owner-inputs.yaml`
- `.env`
- `BUILD_LOG.md` (growing)
- `services/**` (all source code, via sub-agents)
- `config/caddy/`, `config/authentik/`, `config/retrieval/`, `config/prometheus/`, etc.
- `scripts/`
- `migrations/`
- `tests/`
- `docs/eval/baseline-YYYY-MM-DD.md`

Modified:
- `HANDOVER.md` (populated at Phase 10)
- `Makefile` (filled in by deployment-agent)
- `config/docker-compose.yml` (pinned, completed by deployment-agent)

Not modified (unless superseding ADR is written):
- `docs/01_CONCEPT.md`
- `docs/02_ARCHITECTURE.md` (structural — sections may get minor clarifications, not rewrites)
- Any existing ADR
- `docs/03_HANDBOOK.md`, `docs/04_OPERATIONS.md` (polished at Phase 10, not rewritten)
- `README.md`
- `CLAUDE.md`
