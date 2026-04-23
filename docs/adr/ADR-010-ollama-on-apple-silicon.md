# ADR-010 — Ollama runs natively on Apple Silicon hosts, not in Docker

- **Status:** Accepted
- **Date:** 2026-04-23
- **Supersedes:** partially, the deployment sketch in the first revision of `02_ARCHITECTURE.md` that implied Ollama-in-Compose on every platform.

## Context

The reference workstation is **Apple M4 Max, 64 GB unified memory**. All non-LLM services run happily in Docker (via Docker Desktop or Colima) which internally runs a lightweight Linux VM. Ollama can technically run inside that VM — but **cannot access Apple's Metal GPU from the VM**:

- Docker Desktop on macOS: no Metal passthrough. Metal is macOS-only.
- Colima on macOS: same — the VM is Linux; no Metal.
- OrbStack / Lima: same story; experimental Vulkan may appear but Metal does not.

Running Ollama in the Linux VM on an Apple Silicon host therefore falls back to **CPU-only** inference. Measured in practice (public reports and internal notes):

| Model | Native (Metal) | In Docker (CPU) | Slowdown |
|-------|----------------|-----------------|----------|
| Gemma 2 9B Q5_K_M | ~35 tok/s | ~3 tok/s | ≈ 12× |
| Qwen 2.5 32B Q4_K_M | ~12 tok/s | ~1.2 tok/s | ≈ 10× |
| Llama 3.3 70B Q4_K_M | ~4 tok/s | ~0.4 tok/s | ≈ 10× |
| bge-m3 embed | ~500 it/s | ~60 it/s | ≈ 8× |

At these ratios, the in-Docker variant is **not usable**. Even a simple lookup would take ~30 s to first token; a 500-token reasoning answer, a minute; a synthesis answer with the 70B tier, upwards of 20 minutes.

On Linux hosts with an NVIDIA GPU, the Docker story is different — GPU passthrough works — and Ollama-in-Compose is fine.

## Decision

On **Apple Silicon** (`Darwin arm64`) hosts, Ollama runs **natively on the host**, started by the macOS user account (`brew install ollama`). The Docker Compose stack is configured to reach it through `host.docker.internal:11434`. The `ollama` service in `docker-compose.yml` is gated behind a Compose profile (`ollama-docker`) that is **off** by default on macOS.

On **Linux** hosts with NVIDIA GPU, the `ollama-docker` profile is **on** and the service runs in the stack with `--gpus all` (or device reservation in Compose).

A `scripts/host-detect.sh` helper, run by `bootstrap.sh`, writes `OLLAMA_MODE=native|docker|external` into `.env` and the Compose file picks the profile accordingly.

The same decision applies to **TEI reranker**: on Apple Silicon there's no working Metal backend for TEI today. We therefore run the reranker via a small **FastAPI + `sentence-transformers`** service (`services/reranker`) that uses PyTorch with Metal (MPS) backend. Same model (`BAAI/bge-reranker-v2-m3`). On Linux GPU, keep TEI.

## Options considered and rejected

- **Keep Ollama in Docker everywhere for consistency.** Rejected — 10× slowdown on Mac makes the whole product unusable. Consistency is not worth unusability.
- **Use MLX / MLX-LM instead of Ollama on Mac.** MLX is excellent on Apple Silicon, often 20-30 % faster than Ollama. But: a second runtime, a second model format, a second OpenAI-compat shim. Ollama covers both macOS (natively) and Linux (in Docker) with the same API; that consistency wins for ops. MLX is noted in ADR-004 as a future optimisation path.
- **Use LM Studio / llama.cpp server directly.** Either works, but Ollama's model catalog, API maturity, and model-eviction behaviour are better matches for our needs. Same API, less work.

## Consequences

Positive:

- On M4 Max: native speed. Measured targets (ADR-004) are reachable, not hypothetical.
- Unified API endpoint: the retrieval and ingestion services don't care whether Ollama is local native or in a sibling container — it's always `OLLAMA_URL`.
- Model pulls and management use the same `ollama` CLI; no new tooling.

Negative:

- One more "install this manually on macOS" step. Mitigation: `scripts/bootstrap.sh` detects, instructs, and refuses to continue until `ollama serve` responds.
- Ollama updates are now a **host** task on macOS, not a `docker compose pull`. Mitigation: documented in `04_OPERATIONS.md` §9.3 (Ollama model upgrades).
- A separate reranker service on Mac (vs TEI on Linux) creates a small code branch. Mitigation: identical HTTP contract; retrieval-api sees one `RERANK_URL` either way.

## Implementation details

### docker-compose.yml

- Move the `ollama` and `tei-reranker` services into a `ollama-docker` profile.
- Add an `extra_hosts: ["host.docker.internal:host-gateway"]` entry on Linux (already the default on Mac) for services that call `OLLAMA_URL`.
- New `services/reranker` with a minimal `sentence-transformers` + FastAPI image; included when `RERANK_MODE=sentence-transformers` in `.env` (default on macOS).

### .env

```
OLLAMA_MODE=native            # native|docker|external
OLLAMA_URL=http://host.docker.internal:11434   # when native on Mac
RERANK_MODE=sentence-transformers               # or "tei"
RERANK_URL=http://reranker:8090                 # internal
FULL_DOC_CONTEXT_THRESHOLD=20480                # tokens — from ADR-009
```

### bootstrap.sh behaviour on macOS

1. Detects Darwin arm64.
2. Checks for `ollama` binary + responding service on `:11434`. If missing, prints:
   > "Install Ollama natively (required on Apple Silicon): `brew install ollama && brew services start ollama`. Re-run this script after it's up."
3. Writes `OLLAMA_MODE=native`, `OLLAMA_URL=http://host.docker.internal:11434`.
4. Writes `RERANK_MODE=sentence-transformers`.
5. Proceeds.

### Acceptance

- **A0.9** (new, Phase 0): on macOS, bootstrap script refuses to proceed without a reachable native Ollama.
- **A3.5** (new, Phase 3): smoke script measures `tok/s` on each tier; logs a warning if below 50 % of the ADR-004 reference figures (catches a mis-installed Ollama).

## Upgrade path

If in the future Docker Desktop exposes Metal to its VM, or Apple ships a Docker runtime with Metal, we remove this exception and return to Ollama-in-Compose everywhere with a one-line change. We review the position at every major Docker Desktop release.
