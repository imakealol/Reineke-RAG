"""Three-layer resolver for the effective reranker settings of a collection.

Resolution order (highest priority first):

  1. Per-collection override stored in ``TenantProjectPrompt``.
  2. Smart default computed from the collection's indexed doc count.
  3. Global default from ``settings``.

The global ``RERANK_ENABLED`` is a killswitch — when ``False`` the resolver
returns ``enabled=False`` regardless of layers 1 and 2. That lets ops kill
all reranking at once (e.g. during model upgrade) without touching DB rows.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .models import Document, TenantProjectPrompt


@dataclass(frozen=True)
class EffectiveRerankSettings:
    enabled: bool
    overfetch_k: int
    model: str
    # Provenance — useful for the admin UI to explain why a value is what
    # it is ("auto: based on 247 documents", "manual override", "global").
    enabled_source: str  # "override" | "smart-default" | "global-killswitch"
    overfetch_k_source: str  # "override" | "smart-default"
    model_source: str  # "override" | "global"
    doc_count: int


def smart_default_overfetch_k(doc_count: int) -> int:
    """Bucketed default for the candidate pool the reranker reorders.

    The actual right K is empirical (sweep with ``RAG_EVAL_OVERFETCH_K``);
    this is a sensible starting point. Buckets chosen to roughly track
    when the bi-encoder Recall@K starts to drop noticeably in our
    observed setups.
    """
    if doc_count < 100:
        return 15
    if doc_count < 1000:
        return 30
    if doc_count < 5000:
        return 50
    return 100


def smart_default_rerank_enabled(doc_count: int) -> bool:
    """Auto-enable reranking once the collection is large enough that
    bi-encoder ranking quality starts to slip. Below the threshold, the
    extra latency rarely buys a meaningful MRR lift."""
    return doc_count >= settings.RERANK_AUTO_ENABLE_MIN_DOCS


def count_indexed_documents(db: Session, *, tenant: str, project: str) -> int:
    """Number of docs in this collection that successfully completed
    ingestion. Smart defaults key off this count.

    Excludes ``deleted``, ``failed``, ``empty``, and ``requires_ocr`` —
    those don't contribute searchable chunks, so they don't influence
    retrieval difficulty either.
    """
    stmt = select(func.count(Document.id)).where(
        Document.tenant == tenant,
        Document.project == project,
        Document.status == "indexed",
    )
    return int(db.scalar(stmt) or 0)


def resolve(
    db: Session,
    *,
    tenant: str,
    project: str,
) -> EffectiveRerankSettings:
    """Resolve all three layers for one collection. One DB hit for the
    doc count, one for the override row — small and cached at the
    connection level."""
    doc_count = count_indexed_documents(db, tenant=tenant, project=project)
    row = db.get(TenantProjectPrompt, (tenant, project))

    # ---- enabled --------------------------------------------------------
    if not settings.RERANK_ENABLED:
        enabled = False
        enabled_source = "global-killswitch"
    elif row is not None and row.rerank_enabled is not None:
        enabled = bool(row.rerank_enabled)
        enabled_source = "override"
    else:
        enabled = smart_default_rerank_enabled(doc_count)
        enabled_source = "smart-default"

    # ---- overfetch_k ----------------------------------------------------
    if row is not None and row.rerank_overfetch_k is not None:
        overfetch_k = int(row.rerank_overfetch_k)
        overfetch_k_source = "override"
    else:
        overfetch_k = smart_default_overfetch_k(doc_count)
        overfetch_k_source = "smart-default"

    # ---- model ----------------------------------------------------------
    if row is not None and row.rerank_model:
        model = row.rerank_model
        model_source = "override"
    else:
        model = settings.RERANK_MODEL
        model_source = "global"

    return EffectiveRerankSettings(
        enabled=enabled,
        overfetch_k=overfetch_k,
        model=model,
        enabled_source=enabled_source,
        overfetch_k_source=overfetch_k_source,
        model_source=model_source,
        doc_count=doc_count,
    )
