"""Cross-encoder reranker for top-K reordering.

bge-reranker-v2-m3 is the default — multilingual, ~568 M parameters, same
vendor as the bge-m3 embedder. The cross-encoder sees each ``(query,
passage)`` pair together (unlike a bi-encoder, which embeds each side
independently) so it produces much sharper relevance scores at the cost
of ~50–200 ms of inference per query.

This module is a tiny façade around the model so the rest of the codebase
can pretend the underlying library doesn't exist. Lazy singleton — the
model is ~2 GB resident, we don't pay for it unless someone actually
calls ``rerank()``. First call after process start takes 5–15 s to load
the weights from disk.
"""

from __future__ import annotations

from threading import Lock
from typing import List, Optional

from .config import settings
from .utils import get_logger

log = get_logger(__name__)


class RerankerError(RuntimeError):
    """Raised when the reranker dependency is missing or the model fails
    to load. Callers should catch this and fall back to bi-encoder-only
    ordering — losing a reranker should not crash a retrieval request."""


_lock = Lock()
# One wrapper per loaded model name. Lets per-collection ``rerank_model``
# overrides actually do something — different collections can route to
# different cross-encoders within one backend process. Each loaded model
# is ~2 GB resident, so in practice we expect 1 (occasionally 2) entries
# here. A warn log fires when crossing 3.
_instances: dict[str, "_RerankerWrapper"] = {}


class _RerankerWrapper:
    """Hides the FlagEmbedding API behind a stable internal surface."""

    def __init__(self, model_name: str) -> None:
        try:
            from FlagEmbedding import FlagReranker
        except ImportError as exc:  # pragma: no cover
            raise RerankerError(
                "FlagEmbedding is not installed. Add it to requirements.txt "
                "or `pip install FlagEmbedding`."
            ) from exc

        log.info("Loading reranker '%s' (lazy first-use)...", model_name)
        # use_fp16 = ~2× faster on MPS/CUDA with negligible ranking impact —
        # we only need the relative order of scores, not their absolute
        # values. On pure CPU it is treated as a hint, often a no-op.
        self._model = FlagReranker(model_name, use_fp16=True)
        self.model_name = model_name
        log.info("Reranker '%s' ready.", model_name)

    def score(self, query: str, passages: List[str]) -> List[float]:
        if not passages:
            return []
        pairs = [[query, p] for p in passages]
        scores = self._model.compute_score(pairs, normalize=True)
        # FlagReranker returns a bare float for a single pair, list otherwise.
        if isinstance(scores, (int, float)):
            scores = [float(scores)]
        return [float(s) for s in scores]


def get_reranker(model_name: Optional[str] = None) -> _RerankerWrapper:
    """Return the wrapper for ``model_name``, loading it on first request.

    Each model name maps to its own in-process instance — so two
    collections configured with two different rerankers each get the
    right one. Switching a collection's model takes effect the next
    time that model is asked for; no restart required.
    """
    name = model_name or settings.RERANK_MODEL
    if name in _instances:
        return _instances[name]
    with _lock:
        if name not in _instances:
            if _instances:  # at least one already loaded; this is #2+
                log.warning(
                    "Loading additional reranker model '%s' — process now has "
                    "%d resident reranker(s); each is ~2 GB.",
                    name, len(_instances) + 1,
                )
            _instances[name] = _RerankerWrapper(name)
    return _instances[name]


def is_loaded(model_name: Optional[str] = None) -> bool:
    """``True`` if at least one (or the named) reranker model is resident.
    Used by ``/health`` so admins can tell whether the next query will
    pay the cold-start cost or not."""
    if model_name is None:
        return bool(_instances)
    return model_name in _instances


def loaded_model_names() -> List[str]:
    """Names of every reranker model currently resident in this process."""
    return list(_instances.keys())


def rerank(
    *,
    query: str,
    passages: List[str],
    model_name: Optional[str] = None,
) -> List[float]:
    """Score each ``(query, passage)`` pair and return the scores list in
    the same order as ``passages``. Higher = more relevant. Caller is
    responsible for sorting the original candidate list by these scores.

    Raises :class:`RerankerError` if the model cannot be loaded. The
    retrieval pipeline catches that and falls back to bi-encoder order so
    one broken model never takes the chat path down.
    """
    return get_reranker(model_name).score(query, passages)
