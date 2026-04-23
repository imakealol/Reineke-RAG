# ADR-004 — Tiered Ollama LLM stack with a router

- **Status:** Accepted
- **Date:** 2026-04-23

## Context

No single LLM is ideal for every query class:

- **Lookups** need speed more than depth.
- **Extractions** need long-context discipline.
- **Table math** needs numerical faithfulness and SQL.
- **Synthesis** across many documents needs capacity and reasoning.

Running a 70 B model for every lookup wastes minutes of user time and thrashes RAM on the reference box (M4 Max, 64 GB). Running a 9 B model for synthesis produces thin answers that miss cross-document connections.

We also want a DE+EN competent tier at every level.

## Decision

We run Ollama with three LLM tiers, plus the embedding model, plus a reranker (served by TEI, not Ollama). A small classifier (the fast model, one prompt) decides the tier per query. The map is a YAML file so operators can tune without code changes.

| Tier | Model (default) | Quant | RAM (~) | Role |
|------|-----------------|-------|---------|------|
| Fast | `gemma2:9b-instruct-q5_K_M` | Q5 | 7.5 GB | lookup, classifier, query rewrite |
| Reasoning | `qwen2.5:32b-instruct-q4_K_M` | Q4 | 20 GB | extraction, table-math answer assembly |
| Heavy | `llama3.3:70b-instruct-q4_K_M` | Q4 | 40 GB | cross-document synthesis |
| Embedder | `bge-m3` | — | 1 GB | dense embeddings |
| Reranker | `BAAI/bge-reranker-v2-m3` (on TEI) | — | 0.6 GB | final ranking |

### Why these three (DE+EN awareness is explicit)

- **Gemma 2 9B instruct** — strong multilingual, snappy, Apple Metal-friendly.
- **Qwen 2.5 32B Instruct** — top class of weight-openly-released models, solid on German; sweet spot of quality-for-hardware at Q4.
- **Llama 3.3 70B Instruct** — best synthesis at this weight class, multilingual; runs on M4 Max 64 GB at Q4 with patience; on 24 GB GPU Linux, we can swap to 70 B at Q4 with Flash-Attention for much better speed.

### Router policy (initial)

```yaml
router:
  - intent: lookup     -> fast
  - intent: extraction -> reasoning        # see ADR-009 for single-doc full-context mode
  - intent: summarize  -> reasoning
  - intent: table-math -> reasoning        # SQL generation + answer assembly
  - intent: synthesis  -> heavy (falls back to reasoning if heavy disabled or too slow)
  - on long context (> 8 k tokens):
      upgrade fast -> reasoning
  - on short context + DE refusal probability high:
      keep reasoning for stability
```

Classification is itself a Gemma 9B call with a 3-shot prompt; budget one call of ~150 tokens per query. The classifier also runs the **scope-vs-intent** labelling introduced in ADR-009.

### Heavy tier on Apple Silicon — honest limits

On the reference M4 Max, Llama 3.3 70B Q4 runs at roughly 4 tok/s, so a 500-token synthesis answer takes ~2 minutes. Users tolerate that occasionally (e.g. weekly report synthesis) but not routinely. We therefore:

- **Default heavy tier on macOS**: `qwen2.5:32b-instruct-q4_K_M` (same as reasoning). Synthesis answers are shorter and map-reduced but finish in 10–30 s.
- **70B opt-in**: set `LLM_PROFILE=heavy-70b` in `.env` to pull and route to `llama3.3:70b-instruct-q4_K_M`. Bootstrap prints a latency warning in this mode.
- **Linux + 24 GB GPU**: heavy tier is `qwen2.5:72b-instruct-q4_K_M` with GPU offload (honest speed: ~15 tok/s).
- **Linux + 80 GB GPU (A100/H100)**: 70B or 72B is the default; fast.

## Options considered and rejected

- **Mistral Nemo 12B / Small 24B** — good candidates for Reasoning tier; kept as a documented alternative (`.env.override`). Qwen 2.5 edges ahead on DE in our informal trials.
- **Phi-3 medium** — strong reasoning per parameter but weaker on German structural output.
- **Use only one model** (e.g. Qwen 2.5 32B everywhere) — simpler, ~25 % slower median, acceptable if hardware is tight. Kept as `LLM_PROFILE=compact`.

## Consequences

Positive:

- Latency matches query value: fast for simple, slow only for hard.
- Clear upgrade paths: swap a single tier without touching pipelines.
- Model eviction works naturally — Ollama lazy-loads; heavy is invoked rarely so it doesn't stay hot.

Negative:

- Three models to maintain, more disk (but fixed); more env to understand.
- A regression in the classifier hurts everyone. Mitigation: classifier prompt is versioned, Langfuse traces every classification with confidence; alerts on class distribution drift.

## Sizing matrix (reference)

| Host | Fast | Reasoning | Heavy (default) | Heavy (70B opt-in) |
|------|------|-----------|-----------------|---------------------|
| M4 Max 64 GB | ✅ 35 tok/s | ✅ 12 tok/s | Qwen 2.5 32B (same as reasoning) | Llama 3.3 70B ≈ 4 tok/s — warn user |
| M4 Pro 48 GB | ✅ | ✅ | Qwen 2.5 32B | not recommended |
| Linux + RTX 4090 24 GB | ✅ | ✅ | Qwen 2.5 32B | 70B at Q3, degraded |
| Linux + A100 80 GB | ✅ | ✅ | Qwen 2.5 72B / Llama 3.3 70B | native, fast |

Three `LLM_PROFILE` values are supported:

- `compact` — Fast + Reasoning only; synthesis routes to Reasoning. Small hosts.
- `default` — Fast + Reasoning + (Heavy = Reasoning). **This is the default on macOS.**
- `heavy-70b` — Fast + Reasoning + (Heavy = Llama 3.3 70B). Explicit opt-in; bootstrap prints a latency warning.

All profiles work with the same retrieval-api code path — only `config/retrieval/models.yaml` differs.
