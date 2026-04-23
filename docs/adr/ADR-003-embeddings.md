# ADR-003 — bge-m3 as the single embedding model

- **Status:** Accepted
- **Date:** 2026-04-23

## Context

The corpus is **German + English** with heavy keyword content (part numbers, German compounds). We need:

1. Strong multilingual retrieval (not English-biased).
2. **Dense AND sparse** capabilities so hybrid retrieval works without bolting on a second model.
3. Long context (≥ 4 k tokens) so structure-aware chunks can be embedded whole.
4. Runnable offline on the reference hardware.

## Options considered

| Model | Dims | Langs | Dense/Sparse | Context | Notes |
|-------|------|-------|--------------|---------|-------|
| **BAAI/bge-m3** | 1024 | 100+ (incl. DE/EN strong) | **both** (dense + sparse + ColBERT multi-vector) | 8192 | One model, three retrieval modes. |
| BAAI/bge-large-en-v1.5 | 1024 | English primarily | dense only | 512 | Strong for EN, weak for DE. |
| intfloat/multilingual-e5-large-instruct | 1024 | Multilingual | dense only | 514 | Good DE; no native sparse. |
| nomic-embed-text | 768 | mostly English | dense only | 8192 | Excellent EN; too weak for our DE usage. |
| Cohere embed v3 / OpenAI text-embedding-3 | n/a | multilingual | dense only | varies | **Cloud** → rejected. |
| jina-embeddings-v3 | 1024 | multilingual | dense only | 8192 | Strong model, open, but no native sparse. |

## Decision

**bge-m3** is used for **both** dense (1024-d cosine) and sparse (lexical-weighted) vectors. Two execution paths because Ollama's embedding API exposes only the dense output of bge-m3:

- **Dense vectors**: Ollama (`POST /api/embeddings`, `model=bge-m3`). Fast, shared with the LLM runtime, no extra container.
- **Sparse vectors**: `FlagEmbedding.BGEM3FlagModel` imported directly inside the Python processes that produce or consume sparse vectors. Specifically:
  - **ingestion-worker** computes sparse at write time.
  - **retrieval-api** computes sparse at query time (≤ 10 ms for a short query on M4 Max).
  - The model weights are shared with Ollama's bge-m3 weights where possible; otherwise FlagEmbedding downloads them once on first run (the offline bundle contains them).

Both vectors are stored side-by-side in Qdrant under named vectors `dense` and `sparse`. RRF fusion at query time.

Decision is conservative: one embedder, one dimension, one retrieval code path, hybrid is free. The minor cost is ~500 MB of additional model-in-process memory per service that computes sparse (worker + retrieval-api) — acceptable on the reference host.

### Why not a third service just for sparse?

A dedicated embedding service (Infinity, TEI) could encapsulate sparse output. We considered it and rejected it for v1 — in-process FlagEmbedding is simpler, avoids a network hop on every query, and doesn't fragment the embedding model story across two runtimes. If profiling shows the in-process footprint matters (e.g. on a future memory-tight host), switching to an Infinity sidecar is a ~100-line refactor with no contract change.

## Consequences

Positive:

- Hybrid out of the box; DE/EN on equal footing.
- Long context fits our structure-aware chunks (up to ~400–800 tokens) with headroom.
- No English bias.

Negative:

- bge-m3 is larger than nomic-embed-text (~1 GB vs ~274 MB). Offline bundle grows by ~700 MB.
- Inference is a bit slower than single-purpose dense models; measured acceptable on M4 Max (~250 texts/s in batches of 32).
- Sparse output uses a token-based weighting that is model-specific; swapping embedder later is a breaking change (→ Phase migration).

## Validation plan

- Phase 8 eval set (50 queries) is the rubric. Compare bge-m3 dense-only vs dense+sparse hybrid; expect a ≥ 5 pp recall@10 uplift. If not, investigate before accepting.
